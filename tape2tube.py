#!/usr/bin/env python3
import argparse
import json
import os
import re
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    return json.loads(path.read_text())


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def parse_args():
    p = argparse.ArgumentParser(description="Upload MP3s as static-image YouTube videos")
    p.add_argument("--config", default="config.json")
    p.add_argument("--audio-dir", default=None)
    p.add_argument("--images-dir", default=None)
    p.add_argument("--only", default=None, help="Process only this MP3 filename (or path)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    return p.parse_args()


def ensure_dir(path):
    path.mkdir(parents=True, exist_ok=True)


def find_mp3s(input_dir):
    files = [p for p in input_dir.iterdir() if p.suffix.lower() == ".mp3"]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def pick_random_jpg(images_dir):
    images = [p for p in images_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"}]
    if not images:
        raise RuntimeError("No .jpg/.jpeg files found in images dir")
    return random.choice(images)


def load_state(state_path):
    return load_json(state_path, default={"uploaded": {}})


def is_new(mp3_path, state):
    key = str(mp3_path.resolve())
    if key not in state["uploaded"]:
        return True
    prev = state["uploaded"][key]
    stat = mp3_path.stat()
    return not (prev.get("size") == stat.st_size and prev.get("mtime") == stat.st_mtime)


def mark_uploaded(mp3_path, state, video_id):
    stat = mp3_path.stat()
    state["uploaded"][str(mp3_path.resolve())] = {
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "video_id": video_id,
        "uploaded_at": int(time.time()),
    }


def format_date(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def get_creation_ts(stat):
    if sys.platform.startswith("win"):
        return getattr(stat, "st_ctime", None)
    return getattr(stat, "st_birthtime", None)


def detect_mp3_rate_kbps(mp3_path):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=bit_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(mp3_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""

    raw = result.stdout.strip()
    if not raw:
        return ""
    try:
        bps = int(raw)
    except ValueError:
        return ""
    if bps <= 0:
        return ""
    return str(round(bps / 1000))


def build_template_context(mp3_path):
    stat = mp3_path.stat()
    creation_ts = get_creation_ts(stat)
    update_ts = stat.st_mtime
    return {
        "filename": mp3_path.name,
        "basename": mp3_path.stem,
        "creation_date": format_date(creation_ts) if creation_ts is not None else "",
        "update_date": format_date(update_ts),
        "filedate": format_date(update_ts),
        "mp3_rate": detect_mp3_rate_kbps(mp3_path),
    }


def render_template(template, context):
    if not template:
        return ""

    def replace(match):
        key = match.group(1).strip()
        return str(context.get(key, ""))

    rendered = re.sub(r"\{([^{}]+)\}", replace, template)
    # If braces are mismatched, return blank instead of raising errors.
    if "{" in rendered or "}" in rendered:
        return ""
    return rendered


def resolve_config_path(base_dir, value, default):
    raw = value if value is not None else default
    p = Path(raw)
    if p.is_absolute():
        return p
    return (base_dir / p).resolve()


def build_video(mp3_path, image_path, out_dir, video_size, waveform):
    ensure_dir(out_dir)
    out_path = out_dir / (mp3_path.stem + ".mp4")
    fps = int(waveform.get("fps", 30))

    if waveform.get("enabled"):
        wf_height = int(waveform.get("height", 200))
        wf_mode = waveform.get("mode", "line")
        wf_color = waveform.get("color", "white@0.8")
        width = video_size.split("x")[0]

        if wf_mode in {"spectrum", "showspectrum"}:
            spectrum_mode = waveform.get("spectrum_mode", "combined")
            spectrum_slide = int(waveform.get("spectrum_slide", 1))
            spectrum_scale = waveform.get("spectrum_scale", "lin")
            filter_complex = (
                f"[0:v]scale={video_size},format=yuv420p[bg];"
                f"[1:a]showspectrum=s={width}x{wf_height}:"
                f"color={wf_color}:slide={spectrum_slide}:"
                f"mode={spectrum_mode}:scale={spectrum_scale}[sw];"
                f"[bg][sw]overlay=(W-w)/2:(H-h)/2"
            )
        else:
            filter_complex = (
                f"[0:v]scale={video_size},format=yuv420p[bg];"
                f"[1:a]showwaves=s={width}x{wf_height}:"
                f"mode={wf_mode}:colors={wf_color}[sw];"
                f"[bg][sw]overlay=(W-w)/2:(H-h)/2"
            )

        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-framerate", str(fps),
            "-i", str(image_path), "-i", str(mp3_path),
            "-filter_complex", filter_complex,
            "-c:v", "libx264", "-tune", "stillimage", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(out_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-framerate", "1",
            "-i", str(image_path), "-i", str(mp3_path),
            "-c:v", "libx264", "-tune", "stillimage",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-pix_fmt", "yuv420p",
            str(out_path),
        ]

    return cmd, out_path


def get_youtube_service(client_secrets, token_path):
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload_video(youtube, video_path, title, description, tags, category_id, privacy_status):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
        },
    }

    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")
    return response.get("id")


def add_to_playlist(youtube, video_id, playlist_id):
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {
                "kind": "youtube#video",
                "videoId": video_id,
            },
        }
    }
    request = youtube.playlistItems().insert(part="snippet", body=body)
    return request.execute().get("id")


def main():
    args = parse_args()
    cfg_path = Path(args.config)
    config = load_json(cfg_path)
    config_dir = cfg_path.resolve().parent

    audio_dir = Path(args.audio_dir) if args.audio_dir else resolve_config_path(config_dir, config.get("audio_dir"), "./audio")
    images_dir = Path(args.images_dir) if args.images_dir else resolve_config_path(config_dir, config.get("images_dir"), "./images")
    out_dir = resolve_config_path(config_dir, config.get("out_dir"), "./out")
    state_path = resolve_config_path(config_dir, config.get("state_path"), "./state.json")
    client_secrets = resolve_config_path(config_dir, config.get("client_secrets"), "./client_secrets.json")
    token_path = resolve_config_path(config_dir, config.get("token_path"), "./token.json")

    title_prefix = config.get("title_prefix", "")
    description = config.get("description", "")
    description_prefix = config.get("description_prefix", "pocket operator tinkering - ")
    title_template = config.get("title_template", "")
    description_template = config.get("description_template", "")
    tags = config.get("tags", [])
    category_id = str(config.get("category_id", "10"))
    privacy_status = config.get("privacy_status", "unlisted")
    playlist_id = config.get("playlist_id", "")
    video_size = config.get("video_size", "1280x720")
    waveform = config.get("waveform", {"enabled": False})

    ensure_dir(out_dir)
    ensure_dir(state_path.parent)

    state = load_state(state_path)

    if args.only:
        only_path = Path(args.only)
        if not only_path.is_absolute():
            only_path = audio_dir / only_path
        if not only_path.exists():
            print(f"Requested MP3 not found: {only_path}")
            return 1
        mp3s = [only_path]
    else:
        mp3s = [p for p in find_mp3s(audio_dir) if is_new(p, state)]

    if args.limit and args.limit > 0:
        mp3s = mp3s[: args.limit]

    if not mp3s:
        print("No new MP3s found")
        return 0

    if args.dry_run:
        print(f"Dry run: {len(mp3s)} new MP3(s) would be processed")
        for mp3 in mp3s:
            print(f"- {mp3.name}")
        return 0

    if not client_secrets.exists():
        print(f"Missing client_secrets.json at {client_secrets}")
        return 1

    youtube = get_youtube_service(client_secrets, token_path)

    total = len(mp3s)
    for idx, mp3 in enumerate(mp3s, start=1):
        print(f"[{idx}/{total}] Starting: {mp3.name}", flush=True)
        image = pick_random_jpg(images_dir)
        cmd, video_path = build_video(mp3, image, out_dir, video_size, waveform)
        print(f"[{idx}/{total}] Rendering: {mp3.name} with {image.name}", flush=True)
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"[{idx}/{total}] Render failed for {mp3.name}: {exc}", flush=True)
            continue

        template_context = build_template_context(mp3)
        rendered_title = render_template(title_template, template_context)
        rendered_desc = render_template(description_template, template_context)

        title = rendered_title if rendered_title else f"{title_prefix}{mp3.stem}"
        if rendered_desc:
            full_description = rendered_desc
            if description:
                full_description = f"{full_description}\n\n{description}"
        else:
            full_description = f"{description_prefix}{mp3.stem}\nRecorded on {template_context['update_date']}"
            if description:
                full_description = f"{full_description}\n\n{description}"

        print(f"[{idx}/{total}] Uploading: {video_path.name}", flush=True)
        try:
            video_id = upload_video(
                youtube,
                video_path,
                title=title,
                description=full_description,
                tags=tags,
                category_id=category_id,
                privacy_status=privacy_status,
            )
        except Exception as exc:
            print(f"[{idx}/{total}] Upload failed for {mp3.name}: {exc}", flush=True)
            continue
        print(f"[{idx}/{total}] Uploaded video ID: {video_id}", flush=True)
        # Persist successful uploads before optional playlist calls.
        mark_uploaded(mp3, state, video_id)
        save_json(state_path, state)

        if playlist_id:
            print(f"[{idx}/{total}] Adding to playlist: {playlist_id}", flush=True)
            try:
                add_to_playlist(youtube, video_id, playlist_id)
            except Exception as exc:
                print(f"[{idx}/{total}] Playlist add failed for {video_id}: {exc}", flush=True)
        print(f"[{idx}/{total}] Done: {mp3.name}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
