from __future__ import annotations

import os
import subprocess
from pathlib import Path

from common import find_ffmpeg, load_config, output_dir


def run_ffmpeg(ffmpeg: str, source_url: str, output_wav: Path) -> None:
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        source_url,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(output_wav),
    ]
    subprocess.run(cmd, check=True)


def main() -> None:
    load_config()

    ffmpeg = find_ffmpeg()
    out = output_dir()
    output_wav = out / "audio.wav"
    audio_url = os.environ["AUDIO_M3U8"]
    video_url = os.environ["VIDEO_M3U8"]

    try:
        print("Downloading audio stream...")
        run_ffmpeg(ffmpeg, audio_url, output_wav)
    except subprocess.CalledProcessError:
        print("Audio stream failed; falling back to video stream audio extraction...")
        run_ffmpeg(ffmpeg, video_url, output_wav)

    size_mb = output_wav.stat().st_size / 1024 / 1024
    print(f"audio.wav={output_wav}")
    print(f"size_mb={size_mb:.2f}")


if __name__ == "__main__":
    main()
