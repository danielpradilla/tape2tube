#!/usr/bin/env python3
import argparse
import json
import os
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


def format_recorded_date(mp3_path):
    ts = mp3_path.stat().st_mtime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


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
    config_path = Path(args.config)
    config = load_json(config_path)

    audio_dir = Path(args.audio_dir or config.get("audio_dir") or "./audio")
    images_dir = Path(args.images_dir or config.get("images_dir") or "./images")
    out_dir = Path(config.get("out_dir", "./out"))
    state_path = Path(config.get("state_path", "./state.json"))
    client_secrets = Path(config.get("client_secrets", "./client_secrets.json"))
    token_path = Path(config.get("token_path", "./token.json"))

    title_prefix = config.get("title_prefix", "")
    description = config.get("description", "")
    description_prefix = config.get("description_prefix", "pocket operator tinkering - ")
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

    for mp3 in mp3s:
        image = pick_random_jpg(images_dir)
        cmd, video_path = build_video(mp3, image, out_dir, video_size, waveform)
        print(f"Rendering: {mp3.name} with {image.name}")
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"Render failed for {mp3.name}: {exc}")
            continue

        title = f"{title_prefix}{mp3.stem}"
        recorded_on = format_recorded_date(mp3)
        full_description = f"{description_prefix}{mp3.stem}\nRecorded on {recorded_on}"
        if description:
            full_description = f"{full_description}\n\n{description}"

        print(f"Uploading: {video_path.name}")
        video_id = upload_video(
            youtube,
            video_path,
            title=title,
            description=full_description,
            tags=tags,
            category_id=category_id,
            privacy_status=privacy_status,
        )
        print(f"Uploaded video ID: {video_id}")

        if playlist_id:
            print(f"Adding to playlist: {playlist_id}")
            add_to_playlist(youtube, video_id, playlist_id)

        mark_uploaded(mp3, state, video_id)
        save_json(state_path, state)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
