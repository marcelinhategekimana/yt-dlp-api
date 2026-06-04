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

# CORS middleware - allow all origins for API access
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

# Handle preflight OPTIONS requests
@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    response = app.make_default_options_response()
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

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

CODE_VERSION = "2026-06-04-v2"  # Increment to verify Railway deployment

@app.route('/health', methods=['GET'])
def health():
    # Don't load model on health check to avoid OOM on free tier
    whisper_installed = False
    try:
        import whisper
        whisper_installed = True
    except ImportError:
        pass

    # Check assets
    assets_dir = os.path.join(os.path.dirname(__file__), 'assets')
    logo_path = os.path.join(assets_dir, 'kmp-logo-v2.png')

    font_path = os.path.join(assets_dir, 'DejaVuSans-Bold.ttf')
    return jsonify({
        'status': 'ok',
        'service': 'reclip-api',
        'version': CODE_VERSION,
        'whisper': whisper_installed,
        'model': os.getenv('WHISPER_MODEL', 'tiny'),
        'gpu': os.getenv('GPU_ENABLED', 'false'),
        'assets_dir': assets_dir,
        'assets_exist': os.path.isdir(assets_dir),
        'logo_exists': os.path.exists(logo_path),
        'logo_path': logo_path,
        'font_exists': os.path.exists(font_path),
        'font_path': font_path
    })


@app.route('/api/test-ffmpeg', methods=['GET'])
def test_ffmpeg():
    """Test FFmpeg text overlay capability with full filter chain"""
    import subprocess

    assets_dir = os.path.join(os.path.dirname(__file__), 'assets')
    font_path = os.path.join(assets_dir, 'DejaVuSans-Bold.ttf')
    font_escaped = font_path.replace('\\', '/').replace(':', '\\:')

    # Create a test video with color bars
    test_input = '/tmp/test_input.mp4'
    test_output = '/tmp/test_output.mp4'

    # Generate test video (2 seconds color bars)
    gen_cmd = [
        'ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=720x1280:d=2',
        '-c:v', 'libx264', '-t', '2', test_input
    ]
    gen_result = subprocess.run(gen_cmd, capture_output=True, text=True, timeout=30)

    if gen_result.returncode != 0:
        return jsonify({
            'success': False,
            'step': 'generate_test_video',
            'error': gen_result.stderr[:500]
        })

    # Try full filter chain matching actual usage (no quotes around text, escaped commas in enable)
    filter_parts = [
        "drawbox=x=0:y=ih*0.5:w=iw:h=ih*0.5:color=0x0047AB@0.6:t=fill",
        f"drawtext=text=KIVU MORNING POST:fontfile={font_escaped}:fontsize=28:fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=20",
        f"drawbox=x=10:y=550:w=iw-20:h=110:color=0x0047AB@0.95:t=fill:enable=between(t\\,0\\,5)",
        f"drawtext=text=TEST TITLE:fontfile={font_escaped}:fontsize=34:fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=590:enable=between(t\\,0\\,5)",
        f"drawtext=text=KIVUMORNINGPOST:fontfile={font_escaped}:fontsize=24:fontcolor=white:borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-55",
        f"drawtext=text=www.kivumorningpost.com:fontfile={font_escaped}:fontsize=18:fontcolor=white:borderw=2:bordercolor=black:x=w-text_w-15:y=h-28"
    ]
    filter_str = ','.join(filter_parts)

    text_cmd = [
        'ffmpeg', '-y', '-i', test_input,
        '-vf', filter_str,
        '-c:v', 'libx264', '-t', '2',
        test_output
    ]
    text_result = subprocess.run(text_cmd, capture_output=True, text=True, timeout=30)

    output_exists = os.path.exists(test_output)
    output_size = os.path.getsize(test_output) if output_exists else 0

    # Clean up
    for f in [test_input, test_output]:
        if os.path.exists(f):
            os.remove(f)

    return jsonify({
        'success': text_result.returncode == 0 and output_size > 0,
        'font_path': font_path,
        'font_exists': os.path.exists(font_path),
        'filter': filter_str[:300] + '...',
        'ffmpeg_returncode': text_result.returncode,
        'ffmpeg_stderr': text_result.stderr[:1000] if text_result.stderr else None,
        'output_exists': output_exists,
        'output_size': output_size
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

        # Download - use yt-dlp for social media URLs, requests for direct URLs
        is_direct_url = video_url.endswith('.mp4') or 'video.twimg.com' in video_url or 'blob:' in video_url

        if is_direct_url:
            response = requests.get(video_url, stream=True, timeout=120)
            with open(video_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        else:
            ydl_opts = {
                'format': 'best[ext=mp4]/best',
                'outtmpl': video_path,
                'quiet': True,
                'no_warnings': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

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

        # Download video - use yt-dlp for social media URLs, requests for direct URLs
        is_direct_url = video_url.endswith('.mp4') or 'video.twimg.com' in video_url or 'blob:' in video_url

        if is_direct_url:
            # Direct video URL - use requests
            response = requests.get(video_url, stream=True, timeout=300)
            with open(video_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        else:
            # Social media URL - use yt-dlp
            ydl_opts = {
                'format': 'best[ext=mp4]/best',
                'outtmpl': video_path,
                'quiet': True,
                'no_warnings': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

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
    response = {
        'status': job.get('status'),
        'progress': job.get('progress', 0),
        'error': job.get('error'),
    }

    # Include transcription data if available
    if 'text' in job:
        response['text'] = job.get('text')
        response['language'] = job.get('language')
        response['segments'] = job.get('segments')
        response['word_timestamps'] = job.get('word_timestamps')
        response['duration'] = job.get('duration')

    # Include transcribed_words count from add-captions jobs
    if 'transcribed_words' in job:
        response['transcribed_words'] = job.get('transcribed_words')

    return jsonify(response)

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
        show_logo = data.get('showLogo', True)  # Control logo visibility
        title_position = data.get('titlePosition', 'bottom')  # top, center, bottom - default to bottom
        highlight_keywords = data.get('highlightKeywords', [])

        # Caption style options: 'bottom' (default), 'top', 'center'
        caption_style = data.get('captionStyle', 'bottom')

        # Skip transcription if explicitly requested
        # Only auto-skip if skipTranscription is not explicitly set to false
        skip_transcription = data.get('skipTranscription', None)
        if skip_transcription is None:
            # Auto-skip if no caption/timestamps provided and branding requested
            skip_transcription = not caption and not word_timestamps and show_branding
        else:
            skip_transcription = bool(skip_transcription)

        if not video_url:
            return jsonify({'error': 'videoUrl required'}), 400

        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {'status': 'processing', 'progress': 0}

        # Start processing in background
        thread = threading.Thread(
            target=process_video_with_overlays,
            args=(job_id, video_url, caption, word_timestamps, title, title_duration,
                  show_branding, show_logo, title_position, highlight_keywords, caption_style, skip_transcription)
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


def generate_ass_captions(word_timestamps, video_width=720, video_height=1280, style='default'):
    """
    Generate ASS subtitles with ClippedAI-style formatting:
    - Large bold font (60px for 720p)
    - Smart word grouping (max 25 chars per line)
    - Top-center positioning
    - Yellow highlighting for numbers/keywords
    - Thick outline for readability
    """

    # ASS header with professional styles for 9:16 vertical video
    # PlayRes: 720x1280 (9:16 format)
    # Bottom captions positioned ABOVE the footer (social icons at ~80px from bottom)
    # MarginV=200 ensures captions are above the KMP footer
    ass_content = """[Script Info]
Title: KMP Captions
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 1
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,38,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,8,15,15,120,1
Style: Yellow,Arial Black,38,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,8,15,15,120,1
Style: Center,Arial Black,38,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,5,15,15,280,1
Style: CenterYellow,Arial Black,38,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,5,15,15,280,1
Style: Bottom,Arial Black,34,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,15,15,200,1
Style: BottomYellow,Arial Black,34,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,15,15,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    if not word_timestamps:
        return ass_content

    # Choose base style based on position preference
    # 'bottom' = captions at bottom (above footer)
    # 'center' = captions in middle of screen (above bottom title)
    # 'top'/default = captions at top
    if style == 'bottom':
        base_style = 'Bottom'
        highlight_style = 'BottomYellow'
    elif style == 'center':
        base_style = 'Center'
        highlight_style = 'CenterYellow'
    else:
        base_style = 'Default'
        highlight_style = 'Yellow'

    # Format timestamp for ASS (H:MM:SS.cc)
    def format_time(t):
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        cs = int((t - int(t)) * 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    # Smart word grouping: max 25 chars per cue, or gap > 0.5s
    cues = []
    current_cue = {
        'words': [],
        'start_time': None,
        'end_time': None
    }

    for w in word_timestamps:
        word = w.get('word', '').strip()
        if not word:
            continue

        start_time = w.get('start', 0)
        end_time = w.get('end', 0)

        should_start_new = False
        if current_cue['start_time'] is None:
            should_start_new = True
        elif len(' '.join(current_cue['words']) + ' ' + word) > 25:
            # Line too long, start new cue
            should_start_new = True
        elif start_time - current_cue['end_time'] > 0.5:
            # Gap too long, start new cue
            should_start_new = True

        if should_start_new:
            # Save previous cue if exists
            if current_cue['words']:
                cues.append(current_cue)
            # Start new cue
            current_cue = {
                'words': [word],
                'start_time': start_time,
                'end_time': end_time
            }
        else:
            # Add to current cue
            current_cue['words'].append(word)
            current_cue['end_time'] = end_time

    # Don't forget the last cue
    if current_cue['words']:
        cues.append(current_cue)

    # Generate dialogue events with smart color highlighting
    for cue in cues:
        start_str = format_time(cue['start_time'])
        end_str = format_time(cue['end_time'])

        # Build text with color highlighting for numbers/currency
        line_parts = []
        for word in cue['words']:
            word_upper = word.upper()
            # Check if word contains numbers or currency
            has_number = any(char.isdigit() for char in word)
            has_currency = '$' in word or '€' in word or '%' in word
            is_formatted_number = ',' in word and word.replace(',', '').replace('.', '').isdigit()

            if has_number or has_currency or is_formatted_number:
                # Apply yellow highlight style
                line_parts.append('{\\rYellow}' + word_upper + '{\\r' + base_style + '}')
            else:
                line_parts.append(word_upper)

        line_text = ' '.join(line_parts)

        ass_content += f"Dialogue: 0,{start_str},{end_str},{base_style},,0,0,0,,{line_text}\n"

    return ass_content


# Keep old function name as alias for backwards compatibility
def generate_ass_karaoke(word_timestamps, video_width=720, video_height=1280):
    """Alias for generate_ass_captions with bottom positioning"""
    return generate_ass_captions(word_timestamps, video_width, video_height, style='bottom')


def generate_srt_from_timestamps(word_timestamps, title, title_duration):
    """Generate SRT content from word timestamps (synced subtitles) - DEPRECATED, use ASS"""
    srt_content = ""
    cue_number = 1

    if not word_timestamps:
        return srt_content

    # Group words into 3-word chunks for display
    for i in range(0, len(word_timestamps), 3):
        words = word_timestamps[i:i+3]
        if not words:
            continue

        text = ' '.join(w['word'] for w in words)
        start = words[0]['start']
        end = words[-1]['end']

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
                                  show_branding, show_logo, title_position, highlight_keywords, caption_style='bottom', skip_transcription=False):
    """Background task to process video: 9:16 ratio + KMP overlays + logo + captions"""
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

        # Auto-transcribe if no word timestamps provided (and not explicitly skipped)
        if (not word_timestamps or len(word_timestamps) == 0) and not skip_transcription:
            print(f"[{job_id}] No timestamps provided, auto-transcribing with Whisper...")
            jobs[job_id]['status'] = 'transcribing'
            jobs[job_id]['progress'] = 10

            try:
                model = get_whisper_model()
                if model:
                    # Extract audio
                    audio_path = f'{work_dir}/audio.wav'
                    audio_result = subprocess.run([
                        'ffmpeg', '-y', '-i', input_path,
                        '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
                        audio_path
                    ], capture_output=True, text=True, timeout=120)

                    if audio_result.returncode != 0:
                        print(f"[{job_id}] Audio extraction failed: {audio_result.stderr[:300]}")
                        raise Exception("Audio extraction failed")

                    if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
                        print(f"[{job_id}] Audio file too small or missing")
                        raise Exception("Audio file invalid")

                    jobs[job_id]['progress'] = 15
                    print(f"[{job_id}] Audio extracted, size: {os.path.getsize(audio_path)}")

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
                    jobs[job_id]['transcribed_words'] = len(word_timestamps)
                else:
                    print(f"[{job_id}] Whisper not available, skipping transcription")
            except Exception as trans_err:
                print(f"[{job_id}] Transcription error: {trans_err}")
                import traceback
                traceback.print_exc()
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

        # No artificial duration cap — keep the full video length. Each
        # platform applies its own Reel limits at upload time (e.g. Facebook
        # Reels ~90s, Instagram Reels ~3min); over-limit uploads are handled
        # gracefully by the publisher (stored + notified, not truncated here).
        trim_args = []

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
            *trim_args,
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
                *trim_args,
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
        # Keep the FULL title — no 50-char truncation (that was cutting titles
        # off mid-word, e.g. "... VILLE M"). This alphanumeric-only version is
        # drawtext-safe and only used by the fallback path below; the main ASS
        # path derives its own richer clean_title from the original `title`.
        safe_title = ' '.join(
            ''.join(c for c in title if c.isalnum() or c in ' -').split()
        ).strip().upper()
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

        # Overlay image path
        overlay_path = os.path.join(assets_dir, 'OVERLAYREELS.png')
        has_overlay = os.path.exists(overlay_path)
        print(f"[{job_id}] Assets: overlay={has_overlay}, logo={has_logo}, show_logo={show_logo}, show_branding={show_branding}")

        if show_branding and has_overlay and show_logo:
            # Simple approach: overlay PNG + title text
            font = os.path.join(assets_dir, 'DejaVuSans-Bold.ttf')
            font_escaped = font.replace('\\', '/').replace(':', '\\:')

            jobs[job_id]['progress'] = 50

            # Step 1: Overlay the branding PNG on video
            overlay_output = f'{work_dir}/with_overlay.mp4'
            overlay_cmd = [
                'ffmpeg', '-y',
                '-i', scaled_path,
                '-i', overlay_path,
                '-filter_complex', '[1:v]scale=720:1280[ovr];[0:v][ovr]overlay=0:0',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                '-c:a', 'aac', '-b:a', '128k',
                overlay_output
            ]
            result = subprocess.run(overlay_cmd, capture_output=True, text=True, timeout=600)

            if result.returncode == 0 and os.path.exists(overlay_output):
                print(f"[{job_id}] Overlay PNG added")
                output_path = overlay_output
            else:
                print(f"[{job_id}] Overlay error: {result.stderr[:300]}")
                output_path = scaled_path

            jobs[job_id]['progress'] = 60

            # Step 2: Add blue title box + title text with yellow keywords (matching preview)
            if safe_title and os.path.exists(output_path):
                title_output = f'{work_dir}/with_title.mp4'

                # Remove emojis from title - keep only alphanumeric, spaces, and basic punctuation
                import re
                # Derive from the ORIGINAL title (not the stripped safe_title)
                # so # and apostrophes survive, and keep the full length — only
                # emojis/control chars are removed.
                clean_title = re.sub(r'[^\w\s\-\'\.,!?#«»:]', '', title, flags=re.UNICODE)
                clean_title = ' '.join(clean_title.split()).strip().upper()

                if not clean_title:
                    clean_title = "ACTUALITES"

                # Auto-detect keywords if none provided
                title_words = clean_title.split()
                if highlight_keywords:
                    keywords_upper = [kw.upper() for kw in highlight_keywords]
                else:
                    # Auto-detect: prepositions, articles, connector words, and numbers for yellow
                    auto_keywords = {'DE', 'LA', 'LE', 'LES', 'DU', 'DES', 'EN', 'AU', 'AUX', 'A', 'ET', 'OU', 'UN', 'UNE', 'EST', 'SONT', 'PAR', 'POUR', 'SUR', 'DANS', 'AVEC'}
                    keywords_upper = []
                    for word in title_words:
                        if any(c.isdigit() for c in word) or word in auto_keywords:
                            keywords_upper.append(word)

                print(f"[{job_id}] Title: {clean_title}, Keywords: {keywords_upper}, Position: {title_position}")

                # Full-width blue title box (720x1280 canvas), left-aligned text.
                FONT_SIZE = 30
                LINE_H = 46           # vertical space per wrapped line
                PAD_X = 22            # text inset from the box's left edge
                PAD_Y = 16            # box padding above/below the text block
                box_x = 8
                box_w = 720 - 2 * box_x  # near-full width, small side margins

                # Wrap to FILL the box width: only break to a new line when the
                # next whole word won't fit (never split a word). The chars that
                # fit per line are derived from the box's inner width and the
                # title font (uppercase glyphs ≈ 0.62 * fontsize wide), so the
                # text uses the whole box instead of leaving the right side empty.
                inner_w = box_w - 2 * PAD_X
                MAX_CHARS_PER_LINE = max(10, int(inner_w / (FONT_SIZE * 0.62)))

                ass_title_parts = []
                line_len = 0
                for word in title_words:
                    if line_len > 0 and line_len + 1 + len(word) > MAX_CHARS_PER_LINE:
                        ass_title_parts.append('\\N')  # break on whole words only
                        line_len = 0
                    elif line_len > 0:
                        ass_title_parts.append(' ')
                        line_len += 1
                    if word in keywords_upper:
                        # Yellow/Gold color in ASS format (BGR): 00D7FF = #FFD700 gold
                        ass_title_parts.append(f"{{\\c&H00D7FF&}}{word}{{\\c&HFFFFFF&}}")
                    else:
                        ass_title_parts.append(word)
                    line_len += len(word)
                ass_title_text = ''.join(ass_title_parts)

                num_lines = ass_title_text.count('\\N') + 1
                box_h = num_lines * LINE_H + 2 * PAD_Y

                # Vertical placement of the box (720x1280 canvas) per position.
                if title_position == 'top':
                    box_y = 70
                elif title_position == 'bottom':
                    box_y = 1040 - box_h  # sit above the bottom branding
                else:  # center
                    box_y = (1280 - box_h) // 2

                text_x = box_x + PAD_X
                text_mid_y = box_y + box_h // 2  # alignment 4 anchors at middle-left

                # ASS: left-aligned text only (no box). BorderStyle=1 gives a
                # black outline + shadow like the preview's text shadow.
                # Alignment 4 = middle-left, so \pos anchors the text block's
                # left-middle and it stays vertically centred in the box.
                title_ass_path = f'{work_dir}/title.ass'
                title_ass_content = f"""[Script Info]
Title: KMP Title
ScriptType: v4.00+
WrapStyle: 2
PlayResX: 720
PlayResY: 1280

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TitleText,Arial Black,{FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,2,4,10,10,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:0{title_duration}.00,TitleText,,0,0,0,,{{\\pos({text_x},{text_mid_y})}}{ass_title_text}
"""
                with open(title_ass_path, 'w', encoding='utf-8') as f:
                    f.write(title_ass_content)

                title_ass_escaped = title_ass_path.replace(':', '\\:')

                # Full-width blue box (#0047AB) + light-blue border (#60A5FA),
                # shown only while the title is on screen, then the text on top.
                box_fill = (
                    f"drawbox=x={box_x}:y={box_y}:w={box_w}:h={box_h}"
                    f":color=0x0047AB:t=fill:enable=between(t\\,0\\,{title_duration})"
                )
                box_border = (
                    f"drawbox=x={box_x}:y={box_y}:w={box_w}:h={box_h}"
                    f":color=0x60A5FA:t=4:enable=between(t\\,0\\,{title_duration})"
                )
                title_vf = f"{box_fill},{box_border},ass={title_ass_escaped}"

                title_cmd = [
                    'ffmpeg', '-y', '-i', output_path,
                    '-vf', title_vf,
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                    '-c:a', 'aac', '-b:a', '128k',
                    title_output
                ]
                result = subprocess.run(title_cmd, capture_output=True, text=True, timeout=600)

                if result.returncode == 0 and os.path.exists(title_output):
                    print(f"[{job_id}] Title with blue box added: {clean_title}")
                    output_path = title_output
                else:
                    print(f"[{job_id}] Title error: {result.stderr[:300]}")

            jobs[job_id]['progress'] = 70

        else:
            # Fallback: just add title text (no overlay image available)
            font = os.path.join(assets_dir, 'DejaVuSans-Bold.ttf')
            font_escaped = font.replace('\\', '/').replace(':', '\\:')

            if safe_title and show_branding:
                filter_str = f"drawtext=text={safe_title}:fontfile={font_escaped}:fontsize=36:fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=(h/2)-20:enable=between(t\\,0\\,{title_duration})"

                fallback_output = f'{work_dir}/fallback_title.mp4'
                fallback_cmd = [
                    'ffmpeg', '-y', '-i', scaled_path,
                    '-vf', filter_str,
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                    '-c:a', 'aac', '-b:a', '128k',
                    fallback_output
                ]
                result = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=600)
                if result.returncode == 0 and os.path.exists(fallback_output):
                    output_path = fallback_output
                else:
                    print(f"[{job_id}] Fallback title error: {result.stderr[:300]}")
                    output_path = scaled_path
            else:
                output_path = scaled_path

        jobs[job_id]['progress'] = 80

        # Add subtitles if we have captions - use ASS for karaoke-style word highlighting
        print(f"[{job_id}] Caption check: caption={bool(caption)}, word_timestamps={len(word_timestamps) if word_timestamps else 0}")
        if caption or (word_timestamps and len(word_timestamps) > 0):
            final_output = f'{work_dir}/final_with_subs.mp4'

            if word_timestamps and len(word_timestamps) > 0:
                # Use ASS format with ClippedAI-style captions
                ass_content = generate_ass_captions(word_timestamps, style=caption_style)
                sub_path = f'{work_dir}/captions.ass'
                with open(sub_path, 'w', encoding='utf-8') as f:
                    f.write(ass_content)
                print(f"[{job_id}] ASS captions created: {len(word_timestamps)} words, style={caption_style}")

                # Escape path for FFmpeg
                sub_escaped = sub_path.replace(':', '\\:')
                sub_cmd = [
                    'ffmpeg', '-y', '-i', output_path,
                    '-vf', f"ass={sub_escaped}",
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                    '-c:a', 'aac', '-b:a', '128k',
                    final_output
                ]
            else:
                # Fallback to SRT for plain caption text
                srt_content = generate_srt_from_caption(caption, '', 0, video_duration)
                sub_path = f'{work_dir}/captions.srt'
                with open(sub_path, 'w', encoding='utf-8') as f:
                    f.write(srt_content)
                print(f"[{job_id}] SRT captions created: {len(srt_content)} chars")

                sub_escaped = sub_path.replace(':', '\\:')
                sub_cmd = [
                    'ffmpeg', '-y', '-i', output_path,
                    '-vf', f"subtitles={sub_escaped}:force_style='FontName=Arial,FontSize=20,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=3,Outline=2,Shadow=1,MarginV=150,Alignment=2'",
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                    '-c:a', 'aac', '-b:a', '128k',
                    final_output
                ]

            result = subprocess.run(sub_cmd, capture_output=True, text=True, timeout=600)
            if result.returncode == 0 and os.path.exists(final_output):
                output_path = final_output
                print(f"[{job_id}] Karaoke captions burned into video")
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


@app.route('/api/clip-video', methods=['POST'])
def clip_video():
    """
    Extract clip(s) from a video with auto-detection or manual timestamps.

    Params:
    - videoUrl: Source video URL
    - mode: 'auto' (AI detection) or 'manual' (specify times)
    - startTime: Start time in seconds (manual mode)
    - endTime: End time in seconds (manual mode)
    - maxClips: Maximum clips to extract (auto mode, default: 3)
    - minDuration: Minimum clip duration (default: 30)
    - maxDuration: Maximum clip duration (default: 60)
    - addCaptions: Whether to add captions (default: true)
    - captionStyle: Caption style (default: 'bottom')
    """
    try:
        data = request.get_json()
        video_url = data.get('videoUrl')
        mode = data.get('mode', 'manual')  # 'auto' or 'manual'
        start_time = data.get('startTime', 0)
        end_time = data.get('endTime')
        max_clips = data.get('maxClips', 3)
        min_duration = data.get('minDuration', 30)
        max_duration = data.get('maxDuration', 60)
        add_captions = data.get('addCaptions', True)
        caption_style = data.get('captionStyle', 'bottom')
        title = data.get('title', '')

        if not video_url:
            return jsonify({'error': 'videoUrl required'}), 400

        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {'status': 'processing', 'progress': 0}

        # Start clipping in background
        thread = threading.Thread(
            target=clip_video_task,
            args=(job_id, video_url, mode, start_time, end_time,
                  max_clips, min_duration, max_duration, add_captions, caption_style, title)
        )
        thread.start()

        return jsonify({'success': True, 'job_id': job_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 400


def detect_scene_changes(video_path, threshold=0.3):
    """Detect scene changes in video using FFmpeg scene detection"""
    import subprocess
    import json

    try:
        # Use FFmpeg to detect scenes
        cmd = [
            'ffprobe', '-v', 'quiet',
            '-show_entries', 'frame=pts_time,pkt_dts_time',
            '-select_streams', 'v',
            '-of', 'json',
            '-f', 'lavfi',
            f"movie={video_path},select='gt(scene,{threshold})',showinfo"
        ]

        # Simpler approach: use ffmpeg scene detection
        scene_cmd = [
            'ffmpeg', '-i', video_path,
            '-vf', f"select='gt(scene,{threshold})',showinfo",
            '-f', 'null', '-'
        ]

        result = subprocess.run(scene_cmd, capture_output=True, text=True, timeout=120)

        # Parse scene timestamps from ffmpeg output
        scenes = [0.0]  # Always start at 0
        for line in result.stderr.split('\n'):
            if 'pts_time:' in line:
                try:
                    pts_match = line.split('pts_time:')[1].split()[0]
                    pts_time = float(pts_match)
                    if pts_time > scenes[-1] + 5:  # At least 5s apart
                        scenes.append(pts_time)
                except:
                    pass

        return scenes
    except Exception as e:
        print(f"Scene detection error: {e}")
        return [0.0]


def find_best_clips(video_path, transcription_result, min_duration=30, max_duration=60, max_clips=3):
    """
    Find the best clips based on speech density and content.
    Returns list of (start_time, end_time, score) tuples.
    """
    segments = transcription_result.get('segments', [])
    if not segments:
        return []

    # Get video duration
    import subprocess
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'json', video_path],
            capture_output=True, text=True, timeout=30
        )
        import json
        probe_data = json.loads(result.stdout)
        video_duration = float(probe_data.get('format', {}).get('duration', 0))
    except:
        video_duration = segments[-1]['end'] if segments else 60

    # Build clips based on speech segments
    clips = []
    window_start = 0

    while window_start < video_duration - min_duration:
        best_end = window_start + min_duration
        best_score = 0

        # Try different end points
        for end_offset in range(min_duration, min(max_duration + 1, int(video_duration - window_start) + 1), 5):
            window_end = window_start + end_offset

            # Count words in this window
            word_count = 0
            engagement_words = 0
            for seg in segments:
                if seg['start'] >= window_start and seg['end'] <= window_end:
                    words = seg.get('text', '').split()
                    word_count += len(words)
                    for w in words:
                        if any(c.isdigit() for c in w) or '$' in w or '%' in w:
                            engagement_words += 1

            duration = window_end - window_start
            word_density = word_count / duration if duration > 0 else 0
            engagement_ratio = engagement_words / max(word_count, 1)

            # Score: word density (50%) + engagement (30%) + optimal duration (20%)
            duration_score = 1.0 - abs(duration - 45) / 45  # Prefer ~45s clips
            score = word_density * 0.5 + engagement_ratio * 0.3 + max(duration_score, 0) * 0.2

            if score > best_score:
                best_score = score
                best_end = window_end

        if best_score > 0.1:  # Minimum quality threshold
            clips.append((window_start, best_end, best_score))

        # Move window forward
        window_start = best_end + 5  # 5s gap between clips

    # Sort by score and return top clips
    clips.sort(key=lambda x: x[2], reverse=True)
    return clips[:max_clips]


def clip_video_task(job_id, video_url, mode, start_time, end_time,
                    max_clips, min_duration, max_duration, add_captions, caption_style, title):
    """Background task to extract and process video clips"""
    import subprocess
    import requests

    try:
        os.makedirs('/tmp/clips', exist_ok=True)
        work_dir = f'/tmp/clips/{job_id}'
        os.makedirs(work_dir, exist_ok=True)

        jobs[job_id]['status'] = 'downloading'
        jobs[job_id]['progress'] = 5

        # Download video
        print(f"[{job_id}] Downloading video...")
        video_response = requests.get(video_url, stream=True, timeout=300)
        input_path = f'{work_dir}/input.mp4'
        with open(input_path, 'wb') as f:
            for chunk in video_response.iter_content(chunk_size=8192):
                f.write(chunk)

        jobs[job_id]['progress'] = 15

        # Get video duration
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'json', input_path],
            capture_output=True, text=True, timeout=30
        )
        import json as json_module
        probe_data = json_module.loads(result.stdout)
        video_duration = float(probe_data.get('format', {}).get('duration', 60))

        print(f"[{job_id}] Video duration: {video_duration}s")

        clips_to_process = []

        if mode == 'auto':
            # Auto-detect best clips using transcription
            jobs[job_id]['status'] = 'transcribing'
            jobs[job_id]['progress'] = 20

            print(f"[{job_id}] Auto-detecting clips with transcription...")

            # Transcribe first
            model = get_whisper_model()
            if model:
                audio_path = f'{work_dir}/audio.wav'
                subprocess.run([
                    'ffmpeg', '-y', '-i', input_path,
                    '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
                    audio_path
                ], capture_output=True, check=True, timeout=120)

                jobs[job_id]['progress'] = 30

                result = model.transcribe(audio_path, word_timestamps=True, verbose=False)
                jobs[job_id]['progress'] = 50

                # Find best clips based on content
                best_clips = find_best_clips(
                    input_path, result,
                    min_duration=min_duration,
                    max_duration=max_duration,
                    max_clips=max_clips
                )

                if best_clips:
                    for i, (clip_start, clip_end, score) in enumerate(best_clips):
                        # Get word timestamps for this clip
                        clip_words = []
                        for seg in result.get('segments', []):
                            for word in seg.get('words', []):
                                word_start = word.get('start', 0)
                                word_end = word.get('end', 0)
                                if word_start >= clip_start and word_end <= clip_end:
                                    clip_words.append({
                                        'word': word.get('word', '').strip(),
                                        'start': word_start - clip_start,  # Relative to clip start
                                        'end': word_end - clip_start
                                    })

                        clips_to_process.append({
                            'index': i,
                            'start': clip_start,
                            'end': clip_end,
                            'score': score,
                            'word_timestamps': clip_words
                        })

                    print(f"[{job_id}] Found {len(clips_to_process)} clips")
                else:
                    # Fallback: split video into equal chunks
                    print(f"[{job_id}] No good clips found, splitting evenly")
                    chunk_duration = min(max_duration, video_duration / max_clips)
                    for i in range(min(max_clips, int(video_duration / chunk_duration))):
                        clips_to_process.append({
                            'index': i,
                            'start': i * chunk_duration,
                            'end': min((i + 1) * chunk_duration, video_duration),
                            'score': 0.5,
                            'word_timestamps': []
                        })
            else:
                # No Whisper, use scene detection
                print(f"[{job_id}] Whisper not available, using scene detection")
                scenes = detect_scene_changes(input_path)
                for i in range(min(max_clips, len(scenes))):
                    clip_start = scenes[i]
                    clip_end = scenes[i + 1] if i + 1 < len(scenes) else video_duration
                    if clip_end - clip_start >= min_duration:
                        clips_to_process.append({
                            'index': i,
                            'start': clip_start,
                            'end': min(clip_start + max_duration, clip_end),
                            'score': 0.5,
                            'word_timestamps': []
                        })

        else:
            # Manual mode - single clip with specified times
            clip_end_time = end_time if end_time else min(start_time + max_duration, video_duration)
            clips_to_process.append({
                'index': 0,
                'start': start_time,
                'end': clip_end_time,
                'score': 1.0,
                'word_timestamps': []
            })

            # Transcribe the clip portion if captions needed
            if add_captions:
                jobs[job_id]['status'] = 'transcribing'
                model = get_whisper_model()
                if model:
                    # Extract clip audio first
                    clip_audio_path = f'{work_dir}/clip_audio.wav'
                    subprocess.run([
                        'ffmpeg', '-y', '-i', input_path,
                        '-ss', str(start_time), '-t', str(clip_end_time - start_time),
                        '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
                        clip_audio_path
                    ], capture_output=True, check=True, timeout=120)

                    result = model.transcribe(clip_audio_path, word_timestamps=True, verbose=False)

                    clip_words = []
                    for seg in result.get('segments', []):
                        for word in seg.get('words', []):
                            clip_words.append({
                                'word': word.get('word', '').strip(),
                                'start': word.get('start', 0),
                                'end': word.get('end', 0)
                            })
                    clips_to_process[0]['word_timestamps'] = clip_words

        jobs[job_id]['progress'] = 60

        # Process each clip
        output_clips = []
        assets_dir = os.path.join(os.path.dirname(__file__), 'assets')

        for clip_info in clips_to_process:
            clip_idx = clip_info['index']
            clip_start = clip_info['start']
            clip_end = clip_info['end']
            clip_words = clip_info.get('word_timestamps', [])

            jobs[job_id]['status'] = f'processing clip {clip_idx + 1}/{len(clips_to_process)}'
            print(f"[{job_id}] Processing clip {clip_idx + 1}: {clip_start:.1f}s - {clip_end:.1f}s")

            # Extract clip
            clip_path = f'{work_dir}/clip_{clip_idx}.mp4'
            subprocess.run([
                'ffmpeg', '-y', '-i', input_path,
                '-ss', str(clip_start), '-t', str(clip_end - clip_start),
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                clip_path
            ], capture_output=True, check=True, timeout=300)

            # Crop to 9:16
            cropped_path = f'{work_dir}/clip_{clip_idx}_916.mp4'
            scale_filter = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280"
            subprocess.run([
                'ffmpeg', '-y', '-i', clip_path,
                '-vf', scale_filter,
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                '-c:a', 'aac', '-b:a', '128k',
                cropped_path
            ], capture_output=True, check=True, timeout=300)

            output_path = cropped_path

            # Add captions if available
            if add_captions and clip_words:
                ass_content = generate_ass_captions(clip_words, style=caption_style)
                ass_path = f'{work_dir}/clip_{clip_idx}.ass'
                with open(ass_path, 'w', encoding='utf-8') as f:
                    f.write(ass_content)

                captioned_path = f'{work_dir}/clip_{clip_idx}_captioned.mp4'
                ass_escaped = ass_path.replace(':', '\\:')
                subprocess.run([
                    'ffmpeg', '-y', '-i', output_path,
                    '-vf', f"ass={ass_escaped}",
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                    '-c:a', 'copy',
                    captioned_path
                ], capture_output=True, check=True, timeout=300)

                if os.path.exists(captioned_path):
                    output_path = captioned_path
                    print(f"[{job_id}] Added {len(clip_words)} caption words to clip {clip_idx + 1}")

            output_clips.append({
                'index': clip_idx,
                'path': output_path,
                'start': clip_start,
                'end': clip_end,
                'duration': clip_end - clip_start,
                'score': clip_info.get('score', 0),
                'word_count': len(clip_words)
            })

        jobs[job_id]['progress'] = 95

        # Return results
        jobs[job_id] = {
            'status': 'done',
            'progress': 100,
            'clips': [
                {
                    'index': c['index'],
                    'filepath': c['path'],
                    'filename': f"clip_{c['index']}_{job_id}.mp4",
                    'start': c['start'],
                    'end': c['end'],
                    'duration': c['duration'],
                    'score': c['score'],
                    'word_count': c['word_count']
                }
                for c in output_clips
            ],
            'total_clips': len(output_clips)
        }

        print(f"[{job_id}] Clipping complete: {len(output_clips)} clips created")

    except Exception as e:
        print(f"[{job_id}] Clip error: {str(e)}")
        import traceback
        traceback.print_exc()
        jobs[job_id] = {'status': 'error', 'error': str(e)}


@app.route('/api/clip-file/<job_id>/<int:clip_index>', methods=['GET'])
def get_clip_file(job_id, clip_index):
    """Download a specific clip from a completed job"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404

    job = jobs[job_id]
    if job.get('status') != 'done':
        return jsonify({'error': 'Job not complete'}), 400

    clips = job.get('clips', [])
    if clip_index >= len(clips):
        return jsonify({'error': 'Clip not found'}), 404

    clip = clips[clip_index]
    filepath = clip.get('filepath')

    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'Clip file not found'}), 404

    return send_file(
        filepath,
        as_attachment=True,
        download_name=clip.get('filename', f'clip_{clip_index}.mp4')
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
