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
    """Add captions to a video using PupCaps"""
    try:
        data = request.get_json()
        video_url = data.get('videoUrl')
        caption = data.get('caption', '')
        word_timestamps = data.get('wordTimestamps', [])
        title = data.get('title', '')
        title_duration = data.get('titleDuration', 5)  # Title shows for 5 seconds

        if not video_url:
            return jsonify({'error': 'videoUrl required'}), 400

        if not caption and not word_timestamps:
            return jsonify({'error': 'caption or wordTimestamps required'}), 400

        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {'status': 'processing', 'progress': 0}

        # Start processing in background
        thread = threading.Thread(
            target=process_captions,
            args=(job_id, video_url, caption, word_timestamps, title, title_duration)
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


def process_captions(job_id, video_url, caption, word_timestamps, title, title_duration):
    """Background task to add captions to video"""
    import subprocess
    import requests

    try:
        os.makedirs('/tmp/captions', exist_ok=True)
        work_dir = f'/tmp/captions/{job_id}'
        os.makedirs(work_dir, exist_ok=True)

        jobs[job_id]['progress'] = 10

        # Download the video
        print(f"[{job_id}] Downloading video...")
        video_response = requests.get(video_url, stream=True, timeout=120)
        video_path = f'{work_dir}/input.mp4'
        with open(video_path, 'wb') as f:
            for chunk in video_response.iter_content(chunk_size=8192):
                f.write(chunk)

        jobs[job_id]['progress'] = 30

        # Get video duration using ffprobe
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
                capture_output=True, text=True, timeout=30
            )
            video_duration = float(result.stdout.strip()) if result.stdout.strip() else 60
        except:
            video_duration = 60

        # Generate SRT file
        print(f"[{job_id}] Generating SRT...")
        if word_timestamps and len(word_timestamps) > 0:
            srt_content = generate_srt_from_timestamps(word_timestamps, title, title_duration)
        else:
            srt_content = generate_srt_from_caption(caption, title, title_duration, video_duration)

        srt_path = f'{work_dir}/captions.srt'
        with open(srt_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)

        jobs[job_id]['progress'] = 40

        # Run PupCaps to generate MOV overlay
        print(f"[{job_id}] Running PupCaps...")
        overlay_path = f'{work_dir}/overlay.mov'
        css_path = '/app/captions.css'

        # PupCaps command
        pupcaps_cmd = [
            'pupcaps', srt_path,
            '--style', css_path,
            '--output', overlay_path,
            '--width', '1080',
            '--height', '1920',
            '--fps', '30'
        ]

        result = subprocess.run(pupcaps_cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"[{job_id}] PupCaps error: {result.stderr}")
            # Fallback: use FFmpeg's subtitle filter directly
            jobs[job_id]['progress'] = 50
            output_path = f'{work_dir}/output.mp4'

            ffmpeg_cmd = [
                'ffmpeg', '-y',
                '-i', video_path,
                '-vf', f"subtitles={srt_path}:force_style='FontName=Arial,FontSize=24,PrimaryColour=&HFFFFFF,OutlineColour=&H0047AB,BackColour=&H0047AB,Outline=2,Shadow=1,MarginV=100'",
                '-c:a', 'copy',
                output_path
            ]

            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                raise Exception(f"FFmpeg error: {result.stderr}")
        else:
            jobs[job_id]['progress'] = 60

            # Composite overlay onto video using FFmpeg
            print(f"[{job_id}] Compositing video...")
            output_path = f'{work_dir}/output.mp4'

            ffmpeg_cmd = [
                'ffmpeg', '-y',
                '-i', video_path,
                '-i', overlay_path,
                '-filter_complex', '[0:v][1:v]overlay=0:0:shortest=1',
                '-c:a', 'copy',
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                output_path
            ]

            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                raise Exception(f"FFmpeg composite error: {result.stderr}")

        jobs[job_id]['progress'] = 90

        # Verify output exists
        if not os.path.exists(output_path):
            raise Exception("Output file not created")

        jobs[job_id] = {
            'status': 'done',
            'progress': 100,
            'filepath': output_path,
            'filename': f'{job_id}_captioned.mp4'
        }
        print(f"[{job_id}] Caption processing complete!")

    except Exception as e:
        print(f"[{job_id}] Error: {str(e)}")
        jobs[job_id] = {'status': 'error', 'error': str(e)}


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
