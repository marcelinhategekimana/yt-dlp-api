"""
Simple yt-dlp video download API
Deploy to Render.com for free
"""
from flask import Flask, request, jsonify, send_file
import yt_dlp
import os
import uuid
import threading
import time

app = Flask(__name__)

# Store job statuses
jobs = {}

# Cleanup old files periodically
def cleanup_old_files():
    """Remove files older than 1 hour"""
    while True:
        time.sleep(300)  # Check every 5 minutes
        try:
            downloads_dir = '/tmp/downloads'
            if os.path.exists(downloads_dir):
                for f in os.listdir(downloads_dir):
                    filepath = os.path.join(downloads_dir, f)
                    if os.path.isfile(filepath):
                        age = time.time() - os.path.getmtime(filepath)
                        if age > 3600:  # 1 hour
                            os.remove(filepath)
        except Exception as e:
            print(f"Cleanup error: {e}")

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleanup_thread.start()

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'yt-dlp-api'})

@app.route('/api/info', methods=['POST'])
def get_info():
    """Get video information without downloading"""
    try:
        data = request.get_json()
        url = data.get('url')

        if not url:
            return jsonify({'error': 'URL required'}), 400

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # Get available formats
            formats = []
            if info.get('formats'):
                for f in info['formats']:
                    if f.get('vcodec') != 'none' and f.get('ext') == 'mp4':
                        formats.append({
                            'id': f.get('format_id'),
                            'label': f"{f.get('height', '?')}p" if f.get('height') else f.get('format_note', 'Unknown'),
                            'height': f.get('height', 0),
                            'ext': f.get('ext'),
                        })

            # Sort by quality (highest first)
            formats.sort(key=lambda x: x.get('height', 0), reverse=True)

            return jsonify({
                'success': True,
                'title': info.get('title'),
                'description': info.get('description'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
                'formats': formats[:5],  # Top 5 formats
            })

    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/download', methods=['POST'])
def start_download():
    """Start async video download"""
    try:
        data = request.get_json()
        url = data.get('url')
        format_type = data.get('format', 'video')  # 'video' or 'audio'
        format_id = data.get('format_id')

        if not url:
            return jsonify({'error': 'URL required'}), 400

        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {'status': 'downloading', 'progress': 0}

        # Start download in background
        thread = threading.Thread(target=download_video, args=(job_id, url, format_type, format_id))
        thread.start()

        return jsonify({'success': True, 'job_id': job_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 400

def download_video(job_id, url, format_type, format_id):
    """Background download task"""
    try:
        os.makedirs('/tmp/downloads', exist_ok=True)
        output_path = f'/tmp/downloads/{job_id}'

        def progress_hook(d):
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                downloaded = d.get('downloaded_bytes', 0)
                if total > 0:
                    jobs[job_id]['progress'] = int((downloaded / total) * 100)

        if format_type == 'audio':
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': output_path + '.%(ext)s',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'progress_hooks': [progress_hook],
                'quiet': True,
            }
        else:
            format_str = 'best[ext=mp4]/best'
            if format_id:
                format_str = f'{format_id}+bestaudio/best[ext=mp4]/best'

            ydl_opts = {
                'format': format_str,
                'outtmpl': output_path + '.%(ext)s',
                'merge_output_format': 'mp4',
                'progress_hooks': [progress_hook],
                'quiet': True,
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find the downloaded file
        for ext in ['mp4', 'mp3', 'webm', 'mkv']:
            filepath = f'{output_path}.{ext}'
            if os.path.exists(filepath):
                jobs[job_id] = {
                    'status': 'done',
                    'progress': 100,
                    'filepath': filepath,
                    'filename': os.path.basename(filepath),
                }
                return

        jobs[job_id] = {'status': 'error', 'error': 'File not found after download'}

    except Exception as e:
        jobs[job_id] = {'status': 'error', 'error': str(e)}

@app.route('/api/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """Get download job status"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404

    job = jobs[job_id]
    return jsonify({
        'status': job.get('status'),
        'progress': job.get('progress', 0),
        'error': job.get('error'),
    })

@app.route('/api/file/<job_id>', methods=['GET'])
def get_file(job_id):
    """Download the completed file"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404

    job = jobs[job_id]
    if job.get('status') != 'done':
        return jsonify({'error': 'Download not complete'}), 400

    filepath = job.get('filepath')
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    return send_file(
        filepath,
        as_attachment=True,
        download_name=job.get('filename', 'video.mp4')
    )

@app.route('/api/direct', methods=['POST'])
def direct_download():
    """Direct download - returns video URL immediately (faster for simple cases)"""
    try:
        data = request.get_json()
        url = data.get('url')

        if not url:
            return jsonify({'error': 'URL required'}), 400

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'best[ext=mp4]/best',
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # Get direct video URL
            video_url = info.get('url')
            if not video_url and info.get('formats'):
                # Find best mp4 format
                for f in reversed(info['formats']):
                    if f.get('url') and f.get('ext') == 'mp4':
                        video_url = f['url']
                        break
                if not video_url:
                    video_url = info['formats'][-1].get('url')

            return jsonify({
                'success': True,
                'url': video_url,
                'title': info.get('title'),
                'description': info.get('description'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
            })

    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/add-captions', methods=['POST'])
def add_captions():
    """Add captions and overlays to a video, ensuring 9:16 output"""
    try:
        data = request.get_json()
        video_url = data.get('videoUrl')
        caption = data.get('caption', '')
        word_timestamps = data.get('wordTimestamps', [])
        title = data.get('title', '')
        title_duration = data.get('titleDuration', 5)

        # Overlay options
        show_branding = data.get('showBranding', True)
        title_position = data.get('titlePosition', 'center')  # top, center, bottom
        highlight_keywords = data.get('highlightKeywords', [])

        if not video_url:
            return jsonify({'error': 'videoUrl required'}), 400

        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {'status': 'processing', 'progress': 0}

        # Start processing in background
        thread = threading.Thread(
            target=process_video_with_overlays,
            args=(job_id, video_url, caption, word_timestamps, title, title_duration,
                  show_branding, title_position, highlight_keywords)
        )
        thread.start()

        return jsonify({'success': True, 'job_id': job_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 400


def generate_srt_from_caption(caption, title, title_duration, video_duration=60):
    """Generate SRT content from plain caption text"""
    srt_content = ""
    cue_number = 1

    # Add title as first subtitle (first N seconds)
    if title:
        srt_content += f"{cue_number}\n"
        srt_content += f"00:00:00,000 --> 00:00:{title_duration:02d},000\n"
        srt_content += f"{title.upper()}\n\n"
        cue_number += 1

    # Split caption into 4-word chunks
    words = caption.strip().split()
    if not words:
        return srt_content

    chunk_size = 4
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunks.append(' '.join(words[i:i + chunk_size]))

    if not chunks:
        return srt_content

    # Calculate timing for each chunk (after title)
    start_time = title_duration if title else 0
    available_duration = video_duration - start_time
    chunk_duration = max(2, available_duration / len(chunks))  # At least 2s per chunk

    for i, chunk in enumerate(chunks):
        start = start_time + (i * chunk_duration)
        end = start + chunk_duration

        # Format timestamps as HH:MM:SS,mmm
        start_h, start_m = divmod(int(start), 3600)
        start_m, start_s = divmod(start_m, 60)
        start_ms = int((start - int(start)) * 1000)

        end_h, end_m = divmod(int(end), 3600)
        end_m, end_s = divmod(end_m, 60)
        end_ms = int((end - int(end)) * 1000)

        srt_content += f"{cue_number}\n"
        srt_content += f"{start_h:02d}:{start_m:02d}:{start_s:02d},{start_ms:03d} --> {end_h:02d}:{end_m:02d}:{end_s:02d},{end_ms:03d}\n"
        srt_content += f"{chunk.upper()}\n\n"
        cue_number += 1

    return srt_content


def generate_srt_from_timestamps(word_timestamps, title, title_duration):
    """Generate SRT content from word timestamps (synced subtitles)"""
    srt_content = ""
    cue_number = 1

    # Add title as first subtitle
    if title:
        srt_content += f"{cue_number}\n"
        srt_content += f"00:00:00,000 --> 00:00:{title_duration:02d},000\n"
        srt_content += f"{title.upper()}\n\n"
        cue_number += 1

    if not word_timestamps:
        return srt_content

    # Group words into 2-word chunks for display
    for i in range(0, len(word_timestamps), 2):
        word1 = word_timestamps[i]
        word2 = word_timestamps[i + 1] if i + 1 < len(word_timestamps) else None

        text = word1['word']
        start = word1['start']
        end = word1['end']

        if word2:
            text += ' ' + word2['word']
            end = word2['end']

        # Format timestamps
        start_h, start_m = divmod(int(start), 3600)
        start_m, start_s = divmod(start_m, 60)
        start_ms = int((start - int(start)) * 1000)

        end_h, end_m = divmod(int(end), 3600)
        end_m, end_s = divmod(end_m, 60)
        end_ms = int((end - int(end)) * 1000)

        srt_content += f"{cue_number}\n"
        srt_content += f"{start_h:02d}:{start_m:02d}:{start_s:02d},{start_ms:03d} --> {end_h:02d}:{end_m:02d}:{end_s:02d},{end_ms:03d}\n"
        srt_content += f"{text.upper()}\n\n"
        cue_number += 1

    return srt_content


def process_video_with_overlays(job_id, video_url, caption, word_timestamps, title, title_duration,
                                  show_branding, title_position, highlight_keywords):
    """Background task to process video: 9:16 ratio + overlays + captions"""
    import subprocess
    import requests

    try:
        os.makedirs('/tmp/captions', exist_ok=True)
        work_dir = f'/tmp/captions/{job_id}'
        os.makedirs(work_dir, exist_ok=True)

        jobs[job_id]['progress'] = 5

        # Download the video
        print(f"[{job_id}] Downloading video...")
        video_response = requests.get(video_url, stream=True, timeout=120)
        input_path = f'{work_dir}/input_original.mp4'
        with open(input_path, 'wb') as f:
            for chunk in video_response.iter_content(chunk_size=8192):
                f.write(chunk)

        jobs[job_id]['progress'] = 15

        # Get video info using ffprobe
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                 '-show_entries', 'stream=width,height,duration', '-show_entries', 'format=duration',
                 '-of', 'json', input_path],
                capture_output=True, text=True, timeout=30
            )
            import json
            probe_data = json.loads(result.stdout)
            input_width = probe_data.get('streams', [{}])[0].get('width', 1080)
            input_height = probe_data.get('streams', [{}])[0].get('height', 1920)
            video_duration = float(probe_data.get('format', {}).get('duration', 60))
        except:
            input_width, input_height, video_duration = 1080, 1920, 60

        print(f"[{job_id}] Input video: {input_width}x{input_height}, duration: {video_duration}s")

        jobs[job_id]['progress'] = 20

        # Step 1: Convert to FULL 9:16 (720x1280) - CROP to fill, no black bars
        print(f"[{job_id}] Converting to 9:16 (crop to fill)...")
        scaled_path = f'{work_dir}/scaled_916.mp4'

        target_w, target_h = 720, 1280

        # Scale to fill and crop - NO black bars
        # scale=720:1280:force_original_aspect_ratio=increase crops to fill
        scale_filter = f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h}"

        scale_cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-vf', scale_filter,
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart',
            scaled_path
        ]
        result = subprocess.run(scale_cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"[{job_id}] Scale error: {result.stderr[:500]}")
            # Fallback: simple scale (may distort)
            scale_cmd = [
                'ffmpeg', '-y', '-i', input_path,
                '-vf', f'scale={target_w}:{target_h}',
                '-c:v', 'libx264', '-preset', 'ultrafast',
                '-c:a', 'aac',
                scaled_path
            ]
            subprocess.run(scale_cmd, capture_output=True, text=True, timeout=600)

        if not os.path.exists(scaled_path):
            print(f"[{job_id}] Scale failed, using original")
            scaled_path = input_path

        jobs[job_id]['progress'] = 35

        # Step 2+3 Combined: Add branding overlays AND captions in single pass
        print(f"[{job_id}] Adding branding and captions...")
        output_path = f'{work_dir}/output.mp4'

        # Build combined filter - use simple syntax without fontfile
        filters = []

        if show_branding:
            # Escape title for FFmpeg - remove problematic chars
            safe_title = ''.join(c for c in title if c.isalnum() or c in ' -').strip()[:50].upper()
            if not safe_title:
                safe_title = "ACTUALITES"

            print(f"[{job_id}] Title: {safe_title}")

            # Top branding: KIVU MORNING POST (white text with black outline)
            filters.append(
                "drawtext=text='KIVU MORNING POST':fontsize=22:fontcolor=white:borderw=2:bordercolor=black:x=(w-text_w)/2:y=20"
            )

            # Title box position
            if title_position == 'top':
                box_y = 70
            elif title_position == 'bottom':
                box_y = 'h-180'
            else:  # center
                box_y = '(h-100)/2'

            # Blue box behind title
            filters.append(f"drawbox=x=10:y={box_y}:w=w-20:h=100:color=blue@0.85:t=fill")

            # Title text (white on blue box)
            filters.append(
                f"drawtext=text='{safe_title}':fontsize=28:fontcolor=white:borderw=1:bordercolor=black:x=(w-text_w)/2:y={box_y}+35"
            )

            # Bottom branding: KIVUMORNINGPOST
            filters.append(
                "drawtext=text='KIVUMORNINGPOST':fontsize=16:fontcolor=white:borderw=1:bordercolor=black:x=(w-text_w)/2:y=h-35"
            )

        jobs[job_id]['progress'] = 45

        # Generate SRT if we have captions
        srt_path = None
        if caption or (word_timestamps and len(word_timestamps) > 0):
            if word_timestamps and len(word_timestamps) > 0:
                srt_content = generate_srt_from_timestamps(word_timestamps, '', 0)
            else:
                srt_content = generate_srt_from_caption(caption, '', 0, video_duration)

            srt_path = f'{work_dir}/captions.srt'
            with open(srt_path, 'w', encoding='utf-8') as f:
                f.write(srt_content)
            print(f"[{job_id}] SRT created: {len(srt_content)} chars")

        jobs[job_id]['progress'] = 55

        # Build final filter string
        filter_str = ','.join(filters) if filters else None

        # Try with overlays first
        success = False
        if filter_str:
            print(f"[{job_id}] Applying overlays: {filter_str[:150]}...")

            overlay_cmd = [
                'ffmpeg', '-y', '-i', scaled_path,
                '-vf', filter_str,
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                '-c:a', 'copy',
                output_path
            ]
            result = subprocess.run(overlay_cmd, capture_output=True, text=True, timeout=600)

            if result.returncode == 0 and os.path.exists(output_path):
                print(f"[{job_id}] Overlays applied successfully")
                success = True
            else:
                print(f"[{job_id}] Overlay error: {result.stderr[:500]}")

        # If overlays failed or no overlays, add subtitles to scaled video
        if not success:
            print(f"[{job_id}] Using scaled video (overlays failed or not requested)")
            if srt_path and os.path.exists(srt_path):
                sub_cmd = [
                    'ffmpeg', '-y', '-i', scaled_path,
                    '-vf', f"subtitles={srt_path}",
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                    '-c:a', 'copy',
                    output_path
                ]
                result = subprocess.run(sub_cmd, capture_output=True, text=True, timeout=600)
                if result.returncode != 0:
                    print(f"[{job_id}] Subtitle error: {result.stderr[:300]}")
                    output_path = scaled_path
            else:
                output_path = scaled_path

        # If we have both overlays and subtitles, add subtitles as second pass
        if success and srt_path and os.path.exists(srt_path):
            print(f"[{job_id}] Adding subtitles to overlaid video...")
            temp_output = f'{work_dir}/final_with_subs.mp4'
            sub_cmd = [
                'ffmpeg', '-y', '-i', output_path,
                '-vf', f"subtitles={srt_path}:force_style='FontSize=22,MarginV=80,PrimaryColour=&HFFFFFF,BackColour=&H80000000,BorderStyle=4'",
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                '-c:a', 'copy',
                temp_output
            ]
            result = subprocess.run(sub_cmd, capture_output=True, text=True, timeout=600)
            if result.returncode == 0 and os.path.exists(temp_output):
                output_path = temp_output
                print(f"[{job_id}] Subtitles added")
            else:
                print(f"[{job_id}] Subtitle pass failed: {result.stderr[:200]}")

        jobs[job_id]['progress'] = 90

        # Verify output exists
        if not os.path.exists(output_path):
            output_path = branded_path if os.path.exists(branded_path) else scaled_path

        jobs[job_id] = {
            'status': 'done',
            'progress': 100,
            'filepath': output_path,
            'filename': f'{job_id}_916_branded.mp4'
        }
        print(f"[{job_id}] Video processing complete: 9:16 with overlays!")

    except Exception as e:
        print(f"[{job_id}] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        jobs[job_id] = {'status': 'error', 'error': str(e)}


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
