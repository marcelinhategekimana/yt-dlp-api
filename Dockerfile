FROM python:3.11-slim

# Install system dependencies: ffmpeg, Node.js, and Puppeteer requirements
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    gnupg \
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
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20.x
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Set Puppeteer to use system Chromium
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# Install PupCaps globally
RUN npm install -g pupcaps@latest

# Install Python dependencies
RUN pip install --no-cache-dir yt-dlp flask gunicorn requests

WORKDIR /app

# Copy application files
COPY app.py .
COPY captions.css .

EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "300", "--workers", "2", "app:app"]
