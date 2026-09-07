"""Grabs short audio/frame samples from a live YouTube stream via yt-dlp + ffmpeg."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(
            f"'{name}' was not found on PATH. It is a system dependency of this "
            f"tool (not installed via pip) -- see the README for install instructions."
        )


def resolve_stream_url(youtube_url: str) -> str:
    """Resolve a direct, playable stream URL for a live YouTube video via yt-dlp."""
    _require_binary("yt-dlp")
    result = subprocess.run(
        ["yt-dlp", "-g", "-f", "best[ext=mp4]/best", youtube_url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed to resolve {youtube_url}: {result.stderr.strip()}")
    urls = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not urls:
        raise RuntimeError(f"yt-dlp returned no stream URL for {youtube_url}")
    return urls[0]


def capture_segment(stream_url: str, out_dir: Path, seconds: int = 30) -> tuple[Path, Path]:
    """Capture `seconds` of mono 16kHz audio (wav) and one frame (jpg) from the stream."""
    _require_binary("ffmpeg")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    audio_path = out_dir / f"audio_{ts}.wav"
    frame_path = out_dir / f"frame_{ts}.jpg"

    audio_result = subprocess.run(
        ["ffmpeg", "-y", "-i", stream_url, "-t", str(seconds),
         "-vn", "-ac", "1", "-ar", "16000", str(audio_path)],
        capture_output=True,
    )
    if audio_result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio capture failed: {audio_result.stderr.decode(errors='replace')}")

    frame_result = subprocess.run(
        ["ffmpeg", "-y", "-i", stream_url, "-frames:v", "1", str(frame_path)],
        capture_output=True,
    )
    if frame_result.returncode != 0:
        raise RuntimeError(f"ffmpeg frame capture failed: {frame_result.stderr.decode(errors='replace')}")

    return audio_path, frame_path
