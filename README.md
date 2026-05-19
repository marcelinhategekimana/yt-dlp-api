# yt-dlp Video Download API

Free video download service using yt-dlp. Supports Twitter/X, YouTube, TikTok, Instagram, and 1000+ sites.

## Deploy to Render (FREE)

1. **Push to GitHub:**
   ```bash
   cd reclip-service
   git init
   git add .
   git commit -m "yt-dlp API service"
   # Create repo on GitHub, then:
   git remote add origin https://github.com/YOUR_USERNAME/yt-dlp-api.git
   git push -u origin main
   ```

2. **Deploy on Render:**
   - Go to [render.com](https://render.com)
   - Click "New" → "Web Service"
   - Connect your GitHub repo
   - Render will auto-detect the Dockerfile
   - Select **Free** plan
   - Click "Create Web Service"

3. **Update KMPai:**
   Once deployed, update the RECLIP_URL in your Supabase function:
   ```
   const RECLIP_URL = 'https://your-service-name.onrender.com';
   ```

## API Endpoints

### GET /health
Health check endpoint.

### POST /api/info
Get video information without downloading.
```json
{ "url": "https://twitter.com/user/status/123" }
```

### POST /api/direct
Quick download - returns direct video URL.
```json
{ "url": "https://twitter.com/user/status/123" }
```

Response:
```json
{
  "success": true,
  "url": "https://video.twimg.com/...",
  "title": "Video title",
  "thumbnail": "https://...",
  "uploader": "username"
}
```

### POST /api/download
Start async download job (for large videos).
```json
{ "url": "...", "format": "video" }
```

### GET /api/status/{job_id}
Check download progress.

### GET /api/file/{job_id}
Download completed file.

## Supported Sites

- Twitter/X ✅
- YouTube ✅
- TikTok ✅
- Instagram ✅
- Facebook ✅
- Reddit ✅
- And 1000+ more (via yt-dlp)

## Free Tier Limits

Render free tier:
- 750 hours/month (enough for ~1 instance 24/7)
- Spins down after 15 min inactivity (cold start ~30s)
- 512MB RAM

## Local Development

```bash
docker build -t yt-dlp-api .
docker run -p 8080:8080 yt-dlp-api
```
