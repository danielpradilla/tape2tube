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
- `ffprobe` on PATH (usually included with ffmpeg; used for `mp3_rate` template variable)
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

## Windows notes
- `tape2tube.py` works on Windows.
- Install Python 3.9+ and ffmpeg (with ffprobe), and ensure both are on PATH.
- Use PowerShell commands:
  ```powershell
  py -3 -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  py -3 tape2tube.py --config config.json
  ```
- `creation_date` uses the file creation time on Windows. On Linux this may be blank if the filesystem does not expose creation time.

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
  "title_template": "{basename}",
  "description_prefix": "",
  "description_template": "{filename} recorded on {creation_date}",
  "description": "",
  "tags": ["cool", "music"],
  "category_id": "10",
  "privacy_status": "unlisted",
  "playlist_id": "",
  "video_size": "1280x720",
  "delete_rendered_files": true,
  "waveform": {
    "enabled": true,
    "mode": "line",
    "color": "black",
    "height": 200,
    "fps": 30
  }
}
```

## Title and Description Templates
You can configure `title_template` and `description_template` with placeholders:
- `{filename}`: full file name, including extension (example: `demo.mp3`)
- `{basename}`: file name without extension (example: `demo`)
- `{creation_date}`: file creation date (`YYYY-MM-DD`) when available
- `{update_date}`: file modification date (`YYYY-MM-DD`)
- `{filedate}`: alias for `{update_date}`
- `{mp3_rate}`: MP3 bitrate in kbps when available (example: `192`)

Examples:
- `title_template`: `{basename}`
- `description_template`: `{filename} recorded on {creation_date}`

If a template variable is invalid (for example `{basname}`), it resolves to blank text. If braces are malformed, the whole rendered template becomes blank and the script falls back to the default title/description behavior.

## Rendered File Cleanup
- `delete_rendered_files` defaults to `true`.
- At startup, old rendered `*.mp4` files in `out_dir` are removed.
- After each successful upload, that file's rendered `.mp4` is deleted.
- Set `delete_rendered_files` to `false` if you want to keep rendered files.

## Demo assets
- `demo/demo.mp3` (copied from `loop1.mp3`)
- `demo/demo-1.jpg`
- `demo/demo-2.jpg`
- `demo/demo-3.jpg`

You can copy these into `audio/` and `images/` to test quickly.

## Notes
- `token.json` is created after the first OAuth login and stores your refresh/access tokens. Keep it private.
- `state.json` tracks uploaded files (path, size, mtime, and video ID) to avoid duplicate uploads.
- Relative paths in `config.json` are resolved from the config file location, which improves portability across shells and operating systems.
- If the API upload limit is hit, you’ll need to wait for YouTube’s daily quota reset.
