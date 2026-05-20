# GPU-enabled Dockerfile for Render with Whisper transcription
FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV GPU_ENABLED=true
ENV WHISPER_MODEL=base

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3-pip \
    ffmpeg \
    curl \
    gnupg \
    # Puppeteer dependencies for headless Chrome
    chromium-browser \
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
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.11 as default
RUN ln -sf /usr/bin/python3.11 /usr/bin/python && \
    ln -sf /usr/bin/pip3 /usr/bin/pip

# Install Node.js 20.x
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Set Puppeteer to use system Chromium
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium-browser

# Install PupCaps globally
RUN npm install -g pupcaps@latest

# Install Python dependencies with CUDA support
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cu121 && \
    pip install --no-cache-dir yt-dlp flask gunicorn requests openai-whisper

# Pre-download Whisper base model
RUN python -c "import whisper; whisper.load_model('base')"

WORKDIR /app

# Copy application files
COPY app.py .
COPY captions.css .

EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "600", "--workers", "1", "--threads", "4", "app:app"]
