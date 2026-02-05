# tape2tube

Convert MP3s into static‑image videos and upload them to YouTube. The tool picks a random JPG/JPEG from an images folder for each MP3, renders an MP4 with ffmpeg, and uploads via the YouTube Data API.

## What it does
- Scans an audio folder for MP3 files (configurable)
- Chooses a random image from an images folder per MP3
- Renders a static‑image video with ffmpeg
- Uploads to YouTube with OAuth
- Tracks uploads in a state file to avoid duplicates

## Requirements
- Python 3.9+
- `ffmpeg` on PATH
- YouTube Data API v3 OAuth client (Desktop app)

Python deps:
- `google-api-python-client`
- `google-auth-httplib2`
- `google-auth-oauthlib`

## Quick start
1. Install deps (recommended in a venv):
   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Create OAuth credentials in Google Cloud and download `client_secrets.json`:
   - Create a Google Cloud project
   - Enable **YouTube Data API v3**
   - Create OAuth credentials (**Desktop app**)
   - Download the JSON and place it at `./client_secrets.json`
3. Copy `config.example.json` to `config.json` and set paths.
4. Run:
   ```bash
   python3 tape2tube.py --config config.json
   ```

## Command line examples
Upload the whole folder:
```bash
python3 tape2tube.py --config config.json
```

Upload just one (the first new MP3 found):
```bash
python3 tape2tube.py --config config.json --limit 1
```

Upload a specific file by name:
```bash
python3 tape2tube.py --config config.json --only demo.mp3
```

## Config (example)
```json
{
  "audio_dir": "./audio",
  "images_dir": "./images",
  "out_dir": "./out",
  "state_path": "./state.json",
  "client_secrets": "./client_secrets.json",
  "token_path": "./token.json",
  "title_prefix": "",
  "description_prefix": "",
  "description": "",
  "tags": ["cool", "music"],
  "category_id": "10",
  "privacy_status": "unlisted",
  "playlist_id": "",
  "video_size": "1280x720",
  "waveform": {
    "enabled": true,
    "mode": "line",
    "color": "black",
    "height": 200,
    "fps": 30
  }
}
```

## Demo assets
- `demo/demo.mp3` (copied from `loop1.mp3`)
- `demo/demo-1.jpg`
- `demo/demo-2.jpg`
- `demo/demo-3.jpg`

You can copy these into `audio/` and `images/` to test quickly.

## Notes
- `token.json` is created after the first OAuth login and stores your refresh/access tokens. Keep it private.
- `state.json` tracks uploaded files (path, size, mtime, and video ID) to avoid duplicate uploads.
- “Recorded on” date can be derived from file metadata (mtime by default).
- If the API upload limit is hit, you’ll need to wait for YouTube’s daily quota reset.
