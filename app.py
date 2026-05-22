"""
yt-dlp video download API with Whisper transcription
Deploy to Render.com with GPU for fast transcription
"""
from flask import Flask, request, jsonify, send_file
import yt_dlp
import os
import uuid
import threading
import time

app = Flask(__name__)

# Lazy load Whisper model (only when needed)
_whisper_model = None

def get_whisper_model():
    """Load Whisper model (cached)"""
    global _whisper_model
    if _whisper_model is None:
        try:
            import whisper
            model_size = os.getenv("WHISPER_MODEL", "base")
            print(f"Loading Whisper model: {model_size}")
            _whisper_model = whisper.load_model(model_size)
            print("Whisper model loaded!")
        except ImportError:
            print("Whisper not installed - transcription disabled")
            return None
    return _whisper_model

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
    # Don't load model on health check to avoid OOM on free tier
    whisper_installed = False
    try:
        import whisper
        whisper_installed = True
    except ImportError:
        pass

    return jsonify({
        'status': 'ok',
        'service': 'reclip-api',
        'whisper': whisper_installed,
        'model': os.getenv('WHISPER_MODEL', 'tiny'),
        'gpu': os.getenv('GPU_ENABLED', 'false')
    })


@app.route('/api/transcribe', methods=['POST'])
def transcribe_video():
    """Transcribe a video using Whisper"""
    try:
        data = request.get_json()
        video_url = data.get('videoUrl') or data.get('url')
        language = data.get('language', 'auto')

        if not video_url:
            return jsonify({'error': 'videoUrl required'}), 400

        model = get_whisper_model()
        if model is None:
            return jsonify({'error': 'Whisper not available'}), 503

        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {'status': 'transcribing', 'progress': 0}

        # Start transcription in background
        thread = threading.Thread(
            target=transcribe_video_task,
            args=(job_id, video_url, language)
        )
        thread.start()

        return jsonify({'success': True, 'job_id': job_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/transcribe/sync', methods=['POST'])
def transcribe_video_sync():
    """Synchronous transcription for short videos"""
    import subprocess
    import requests

    try:
        data = request.get_json()
        video_url = data.get('videoUrl') or data.get('url')
        language = data.get('language', 'auto')

        if not video_url:
            return jsonify({'error': 'videoUrl required'}), 400

        model = get_whisper_model()
        if model is None:
            return jsonify({'error': 'Whisper not available'}), 503

        # Download video to temp file
        work_dir = f'/tmp/transcribe/{uuid.uuid4().hex[:8]}'
        os.makedirs(work_dir, exist_ok=True)

        video_path = f'{work_dir}/video.mp4'
        audio_path = f'{work_dir}/audio.wav'

        # Download
        response = requests.get(video_url, stream=True, timeout=120)
        with open(video_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        # Extract audio
        subprocess.run([
            'ffmpeg', '-y', '-i', video_path,
            '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
            audio_path
        ], capture_output=True, check=True)

        # Transcribe
        options = {"word_timestamps": True, "verbose": False}
        if language != 'auto':
            options["language"] = language

        result = model.transcribe(audio_path, **options)

        # Format response
        segments = [
            {
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"].strip()
            }
            for seg in result.get("segments", [])
        ]

        # Cleanup
        try:
            import shutil
            shutil.rmtree(work_dir)
        except:
            pass

        return jsonify({
            'success': True,
            'text': result.get("text", "").strip(),
            'language': result.get("language", "unknown"),
            'segments': segments,
            'duration': segments[-1]["end"] if segments else 0
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 400


def transcribe_video_task(job_id, video_url, language):
    """Background transcription task"""
    import subprocess
    import requests

    try:
        model = get_whisper_model()
        if model is None:
            jobs[job_id] = {'status': 'error', 'error': 'Whisper not available'}
            return

        jobs[job_id]['progress'] = 10
        jobs[job_id]['status'] = 'downloading'

        work_dir = f'/tmp/transcribe/{job_id}'
        os.makedirs(work_dir, exist_ok=True)

        video_path = f'{work_dir}/video.mp4'
        audio_path = f'{work_dir}/audio.wav'

        # Download video
        response = requests.get(video_url, stream=True, timeout=300)
        with open(video_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        jobs[job_id]['progress'] = 30
        jobs[job_id]['status'] = 'extracting_audio'

        # Extract audio
        subprocess.run([
            'ffmpeg', '-y', '-i', video_path,
            '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
            audio_path
        ], capture_output=True, check=True)

        jobs[job_id]['progress'] = 50
        jobs[job_id]['status'] = 'transcribing'

        # Transcribe with Whisper
        options = {"word_timestamps": True, "verbose": False}
        if language != 'auto':
            options["language"] = language

        result = model.transcribe(audio_path, **options)

        jobs[job_id]['progress'] = 90

        # Format segments
        segments = [
            {
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"].strip()
            }
            for seg in result.get("segments", [])
        ]

        # Word-level timestamps
        word_timestamps = []
        for seg in result.get("segments", []):
            for word in seg.get("words", []):
                word_timestamps.append({
                    "word": word.get("word", "").strip(),
                    "start": word.get("start", 0),
                    "end": word.get("end", 0)
                })

        jobs[job_id] = {
            'status': 'done',
            'progress': 100,
            'text': result.get("text", "").strip(),
            'language': result.get("language", "unknown"),
            'segments': segments,
            'word_timestamps': word_timestamps,
            'duration': segments[-1]["end"] if segments else 0
        }

        # Cleanup
        try:
            import shutil
            shutil.rmtree(work_dir)
        except:
            pass

        print(f"[{job_id}] Transcription complete: {len(segments)} segments")

    except Exception as e:
        print(f"[{job_id}] Transcription error: {e}")
        jobs[job_id] = {'status': 'error', 'error': str(e)}

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
    """Background task to process video: 9:16 ratio + KMP overlays + captions"""
    import subprocess
    import requests

    try:
        os.makedirs('/tmp/captions', exist_ok=True)
        work_dir = f'/tmp/captions/{job_id}'
        os.makedirs(work_dir, exist_ok=True)

        # Asset paths (relative to app.py)
        assets_dir = os.path.join(os.path.dirname(__file__), 'assets')
        logo_path = os.path.join(assets_dir, 'kmp-logo-v2.png')
        butterfly_path = os.path.join(assets_dir, 'kmp-butterfly-broadcast.png')
        social_fb = os.path.join(assets_dir, 'social-facebook.png')
        social_ig = os.path.join(assets_dir, 'social-instagram.png')
        social_tw = os.path.join(assets_dir, 'social-twitter.png')
        social_tt = os.path.join(assets_dir, 'social-tiktok.png')
        social_yt = os.path.join(assets_dir, 'social-youtube.png')

        jobs[job_id]['progress'] = 5
        jobs[job_id]['status'] = 'downloading'

        # Download the video
        print(f"[{job_id}] Downloading video...")
        video_response = requests.get(video_url, stream=True, timeout=120)
        input_path = f'{work_dir}/input_original.mp4'
        with open(input_path, 'wb') as f:
            for chunk in video_response.iter_content(chunk_size=8192):
                f.write(chunk)

        # Auto-transcribe if no word timestamps provided
        if not word_timestamps or len(word_timestamps) == 0:
            print(f"[{job_id}] No timestamps provided, auto-transcribing with Whisper...")
            jobs[job_id]['status'] = 'transcribing'
            jobs[job_id]['progress'] = 10

            try:
                model = get_whisper_model()
                if model:
                    # Extract audio
                    audio_path = f'{work_dir}/audio.wav'
                    subprocess.run([
                        'ffmpeg', '-y', '-i', input_path,
                        '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
                        audio_path
                    ], capture_output=True, check=True, timeout=120)

                    jobs[job_id]['progress'] = 15

                    # Transcribe
                    result = model.transcribe(audio_path, word_timestamps=True, verbose=False)

                    # Extract word-level timestamps
                    word_timestamps = []
                    for seg in result.get("segments", []):
                        for word in seg.get("words", []):
                            word_timestamps.append({
                                "word": word.get("word", "").strip(),
                                "start": word.get("start", 0),
                                "end": word.get("end", 0)
                            })

                    print(f"[{job_id}] Transcribed {len(word_timestamps)} words")
                    jobs[job_id]['progress'] = 20
                else:
                    print(f"[{job_id}] Whisper not available, skipping transcription")
            except Exception as trans_err:
                print(f"[{job_id}] Transcription failed: {trans_err}")
                # Continue without subtitles

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

        # Step 2: Add KMP branding with image overlays
        print(f"[{job_id}] Adding KMP branding overlays...")
        output_path = f'{work_dir}/output.mp4'

        # Escape title for FFmpeg
        safe_title = ''.join(c for c in title if c.isalnum() or c in ' -').strip()[:50].upper()
        if not safe_title:
            safe_title = "ACTUALITES"

        print(f"[{job_id}] Title: {safe_title}")

        # Title box Y position
        if title_position == 'top':
            box_y = 100
        elif title_position == 'bottom':
            box_y = 1050
        else:  # center
            box_y = 550

        # Build FFmpeg command with image overlays
        # Complex filter: video + logo + butterfly + social icons + text overlays
        has_logo = os.path.exists(logo_path)
        has_butterfly = os.path.exists(butterfly_path)
        has_socials = all(os.path.exists(p) for p in [social_fb, social_ig, social_tw, social_tt, social_yt])

        print(f"[{job_id}] Assets: logo={has_logo}, butterfly={has_butterfly}, socials={has_socials}")

        if show_branding and has_logo:
            # Build complex filter with image overlays
            filter_parts = []

            # Blue gradient at bottom (60% height)
            filter_parts.append(f"drawbox=x=0:y=h*0.4:w=w:h=h*0.6:color=0x0047AB@0.7:t=fill")

            # Top: KIVU MORNING POST text
            filter_parts.append(
                "drawtext=text='KIVU MORNING POST':fontsize=18:fontcolor=white:borderw=2:bordercolor=black:x=(w-text_w)/2:y=15"
            )

            # Title blue box with border (only first 5 seconds)
            filter_parts.append(f"drawbox=x=8:y={box_y}:w=w-16:h=90:color=0x0047AB@0.95:t=fill:enable='between(t,0,{title_duration})'")
            filter_parts.append(f"drawbox=x=8:y={box_y}:w=w-16:h=90:color=0x60A5FA:t=3:enable='between(t,0,{title_duration})'")

            # Title text (only first 5 seconds)
            filter_parts.append(
                f"drawtext=text='{safe_title}':fontsize=24:fontcolor=white:borderw=1:bordercolor=black:x=(w-text_w)/2:y={box_y}+30:enable='between(t,0,{title_duration})'"
            )

            # Bottom: KIVUMORNINGPOST text
            filter_parts.append(
                "drawtext=text='KIVUMORNINGPOST':fontsize=14:fontcolor=white:borderw=1:bordercolor=black:x=(w-text_w)/2:y=h-25"
            )

            filter_str = ','.join(filter_parts)

            # First pass: add text overlays
            text_output = f'{work_dir}/with_text.mp4'
            text_cmd = [
                'ffmpeg', '-y', '-i', scaled_path,
                '-vf', filter_str,
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                '-c:a', 'copy',
                text_output
            ]
            result = subprocess.run(text_cmd, capture_output=True, text=True, timeout=600)

            if result.returncode != 0:
                print(f"[{job_id}] Text overlay error: {result.stderr[:300]}")
                text_output = scaled_path

            jobs[job_id]['progress'] = 50

            # Second pass: add logo image overlay (top right)
            if os.path.exists(text_output) and has_logo:
                logo_output = f'{work_dir}/with_logo.mp4'
                logo_cmd = [
                    'ffmpeg', '-y',
                    '-i', text_output,
                    '-i', logo_path,
                    '-filter_complex', '[1:v]scale=60:-1[logo];[0:v][logo]overlay=W-w-15:40',
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                    '-c:a', 'copy',
                    logo_output
                ]
                result = subprocess.run(logo_cmd, capture_output=True, text=True, timeout=600)
                if result.returncode == 0 and os.path.exists(logo_output):
                    print(f"[{job_id}] Logo added")
                    output_path = logo_output
                else:
                    print(f"[{job_id}] Logo overlay error: {result.stderr[:200]}")
                    output_path = text_output
            else:
                output_path = text_output

            jobs[job_id]['progress'] = 60

            # Third pass: add butterfly icon (above social bar)
            if os.path.exists(output_path) and has_butterfly:
                butterfly_output = f'{work_dir}/with_butterfly.mp4'
                bf_cmd = [
                    'ffmpeg', '-y',
                    '-i', output_path,
                    '-i', butterfly_path,
                    '-filter_complex', '[1:v]scale=40:-1[bf];[0:v][bf]overlay=(W-w)/2:H-70',
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                    '-c:a', 'copy',
                    butterfly_output
                ]
                result = subprocess.run(bf_cmd, capture_output=True, text=True, timeout=600)
                if result.returncode == 0 and os.path.exists(butterfly_output):
                    print(f"[{job_id}] Butterfly added")
                    output_path = butterfly_output

            jobs[job_id]['progress'] = 70

            # Fourth pass: add social icons row (bottom)
            if os.path.exists(output_path) and has_socials:
                socials_output = f'{work_dir}/with_socials.mp4'
                # Overlay all 5 social icons in a row at bottom
                social_cmd = [
                    'ffmpeg', '-y',
                    '-i', output_path,
                    '-i', social_fb,
                    '-i', social_ig,
                    '-i', social_tw,
                    '-i', social_tt,
                    '-i', social_yt,
                    '-filter_complex',
                    '[1:v]scale=24:-1[fb];[2:v]scale=24:-1[ig];[3:v]scale=24:-1[tw];[4:v]scale=24:-1[tt];[5:v]scale=24:-1[yt];'
                    '[0:v][fb]overlay=(W/2)-70:H-50[v1];'
                    '[v1][ig]overlay=(W/2)-35:H-50[v2];'
                    '[v2][tw]overlay=(W/2):H-50[v3];'
                    '[v3][tt]overlay=(W/2)+35:H-50[v4];'
                    '[v4][yt]overlay=(W/2)+70:H-50',
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                    '-c:a', 'copy',
                    socials_output
                ]
                result = subprocess.run(social_cmd, capture_output=True, text=True, timeout=600)
                if result.returncode == 0 and os.path.exists(socials_output):
                    print(f"[{job_id}] Social icons added")
                    output_path = socials_output
                else:
                    print(f"[{job_id}] Social icons error: {result.stderr[:200]}")

        else:
            # Fallback: simple text branding only (no image assets)
            filters = []
            if show_branding:
                filters.append("drawtext=text='KIVU MORNING POST':fontsize=22:fontcolor=white:borderw=2:bordercolor=black:x=(w-text_w)/2:y=20")
                # Title box and text only for first N seconds
                filters.append(f"drawbox=x=10:y={box_y}:w=w-20:h=100:color=blue@0.85:t=fill:enable='between(t,0,{title_duration})'")
                filters.append(f"drawtext=text='{safe_title}':fontsize=28:fontcolor=white:borderw=1:bordercolor=black:x=(w-text_w)/2:y={box_y}+35:enable='between(t,0,{title_duration})'")
                filters.append("drawtext=text='KIVUMORNINGPOST':fontsize=16:fontcolor=white:borderw=1:bordercolor=black:x=(w-text_w)/2:y=h-35")

            filter_str = ','.join(filters) if filters else None

            if filter_str:
                fallback_output = f'{work_dir}/fallback_branded.mp4'
                fallback_cmd = [
                    'ffmpeg', '-y', '-i', scaled_path,
                    '-vf', filter_str,
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                    '-c:a', 'copy',
                    fallback_output
                ]
                result = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=600)
                if result.returncode == 0 and os.path.exists(fallback_output):
                    output_path = fallback_output
                else:
                    print(f"[{job_id}] Fallback branding error: {result.stderr[:300]}")
                    output_path = scaled_path
            else:
                output_path = scaled_path

        jobs[job_id]['progress'] = 80

        # Add subtitles if we have captions
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

            # Add subtitles to output - smaller text, positioned above social bar
            final_output = f'{work_dir}/final_with_subs.mp4'
            # Escape path for FFmpeg (replace : with \:)
            srt_escaped = srt_path.replace(':', '\\:')
            sub_cmd = [
                'ffmpeg', '-y', '-i', output_path,
                '-vf', f"subtitles={srt_escaped}:force_style='FontName=Arial,FontSize=16,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=3,Outline=2,Shadow=1,MarginV=120,Alignment=2'",
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                '-c:a', 'copy',
                final_output
            ]
            result = subprocess.run(sub_cmd, capture_output=True, text=True, timeout=600)
            if result.returncode == 0 and os.path.exists(final_output):
                output_path = final_output
                print(f"[{job_id}] Subtitles added")
            else:
                print(f"[{job_id}] Subtitle error: {result.stderr[:200]}")

        jobs[job_id]['progress'] = 95

        # Verify output exists
        if not os.path.exists(output_path):
            output_path = scaled_path

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


@app.route('/api/crop', methods=['POST'])
def crop_video():
    """Crop video to vertical format (9:16)"""
    try:
        data = request.get_json()
        video_url = data.get('videoUrl') or data.get('url') or data.get('inputUrl')
        ratio = data.get('ratio', '9:16')
        mode = data.get('mode', 'center')  # 'center' or 'ai'
        quality = data.get('quality', 'balanced')

        if not video_url:
            return jsonify({'error': 'videoUrl required'}), 400

        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {'status': 'queued', 'progress': 0}

        # Start cropping in background
        thread = threading.Thread(
            target=crop_video_task,
            args=(job_id, video_url, ratio, mode, quality)
        )
        thread.start()

        return jsonify({'success': True, 'jobId': job_id, 'job_id': job_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/probe', methods=['POST'])
def probe_video():
    """Get video dimensions"""
    try:
        data = request.get_json()
        video_url = data.get('videoUrl') or data.get('url')

        if not video_url:
            return jsonify({'error': 'videoUrl required'}), 400

        # Download to temp file for probing
        import requests as req
        work_dir = f'/tmp/downloads/{uuid.uuid4().hex[:8]}'
        os.makedirs(work_dir, exist_ok=True)
        input_path = f'{work_dir}/probe_input.mp4'

        response = req.get(video_url, stream=True, timeout=30)
        with open(input_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        # Get dimensions with ffprobe
        import subprocess
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'json', input_path],
            capture_output=True, text=True
        )

        # Cleanup
        os.remove(input_path)
        os.rmdir(work_dir)

        if result.returncode == 0:
            import json
            info = json.loads(result.stdout)
            stream = info.get('streams', [{}])[0]
            return jsonify({
                'success': True,
                'width': stream.get('width', 1920),
                'height': stream.get('height', 1080)
            })

        return jsonify({'success': False, 'error': 'Could not probe video'}), 400

    except Exception as e:
        return jsonify({'error': str(e)}), 400


def crop_video_task(job_id, video_url, ratio, mode, quality):
    """Background task to crop video to vertical"""
    import subprocess
    import requests as req

    try:
        jobs[job_id]['status'] = 'downloading'
        jobs[job_id]['progress'] = 10

        work_dir = f'/tmp/downloads/{job_id}'
        os.makedirs(work_dir, exist_ok=True)

        input_path = f'{work_dir}/input.mp4'
        output_path = f'{work_dir}/vertical.mp4'

        # Download video
        print(f"[{job_id}] Downloading video...")
        response = req.get(video_url, stream=True, timeout=120)
        response.raise_for_status()

        with open(input_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        jobs[job_id]['status'] = 'analyzing'
        jobs[job_id]['progress'] = 30

        # Get video dimensions
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'json', input_path],
            capture_output=True, text=True
        )

        width, height = 1920, 1080
        if result.returncode == 0:
            import json
            info = json.loads(result.stdout)
            stream = info.get('streams', [{}])[0]
            width = stream.get('width', 1920)
            height = stream.get('height', 1080)

        print(f"[{job_id}] Input dimensions: {width}x{height}")

        # Parse target ratio
        ratio_parts = ratio.split(':')
        target_ratio = int(ratio_parts[0]) / int(ratio_parts[1])

        # Calculate crop
        if width / height > target_ratio:
            # Wider than target - crop sides (center crop)
            new_width = int(height * target_ratio)
            x = (width - new_width) // 2
            crop_filter = f"crop={new_width}:{height}:{x}:0"
        else:
            # Taller than target - crop top/bottom
            new_height = int(width / target_ratio)
            y = (height - new_height) // 2
            crop_filter = f"crop={width}:{new_height}:0:{y}"

        jobs[job_id]['status'] = 'processing'
        jobs[job_id]['progress'] = 50

        # Quality presets
        presets = {
            'fast': {'crf': '28', 'preset': 'veryfast'},
            'balanced': {'crf': '23', 'preset': 'fast'},
            'high': {'crf': '18', 'preset': 'slow'}
        }
        preset = presets.get(quality, presets['balanced'])

        # FFmpeg command with crop and scale to 1080x1920
        print(f"[{job_id}] Cropping with filter: {crop_filter}")
        cmd = [
            'ffmpeg', '-y', '-i', input_path,
            '-vf', f"{crop_filter},scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            '-c:v', 'libx264', '-crf', preset['crf'], '-preset', preset['preset'],
            '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart',
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode == 0 and os.path.exists(output_path):
            jobs[job_id] = {
                'status': 'done',
                'progress': 100,
                'filepath': output_path,
                'filename': f'vertical_{job_id}.mp4',
                'outputWidth': 1080,
                'outputHeight': 1920
            }
            print(f"[{job_id}] Crop complete!")
        else:
            print(f"[{job_id}] FFmpeg error: {result.stderr[:500]}")
            jobs[job_id] = {'status': 'error', 'error': 'FFmpeg processing failed'}

        # Cleanup input
        if os.path.exists(input_path):
            os.remove(input_path)

    except Exception as e:
        print(f"[{job_id}] Crop error: {str(e)}")
        jobs[job_id] = {'status': 'error', 'error': str(e)}


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
