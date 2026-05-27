# CPU-only Dockerfile for Render free tier with Whisper transcription
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV GPU_ENABLED=false
ENV WHISPER_MODEL=tiny

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    gnupg \
    # Font support for FFmpeg text overlays
    fontconfig \
    fonts-dejavu-core \
    # Puppeteer dependencies for headless Chrome
    chromium \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f -v

# Install Node.js 20.x
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Set Puppeteer to use system Chromium
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# Install PupCaps globally
RUN npm install -g pupcaps@latest

# Install Python dependencies (CPU-only PyTorch for smaller image)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir yt-dlp flask gunicorn requests openai-whisper

# Pre-download Whisper tiny model (smallest, works on free tier)
RUN python -c "import whisper; whisper.load_model('tiny')"

WORKDIR /app

# Copy application files
COPY app.py .
COPY captions.css .
COPY assets/ ./assets/

EXPOSE 8080

# Remove healthcheck - Railway handles this
# The Whisper model loading can take 2-3 minutes on first request

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --timeout 600 --workers 1 --threads 2 app:app"]
