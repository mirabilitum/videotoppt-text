from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from common import find_ffmpeg, load_config, output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split audio.wav into wav parts.")
    parser.add_argument(
        "--part-seconds",
        type=int,
        help=(
            "Part length in seconds. Overrides AUDIO_PART_SECONDS. "
            "Use 0 to copy audio.wav as a single part."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_config()

    out = output_dir()
    source = out / "audio.wav"
    if not source.exists():
        raise FileNotFoundError(f"Missing source audio: {source}")

    parts_dir = out / "audio_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    segment_time = (
        args.part_seconds
        if args.part_seconds is not None
        else int(os.getenv("AUDIO_PART_SECONDS", "900"))
    )
    if segment_time < 0:
        raise ValueError("--part-seconds must be 0 or a positive integer.")

    if segment_time == 0:
        target = parts_dir / "audio_part_000.wav"
        for old_part in parts_dir.glob("audio_part_*.wav"):
            if old_part == target:
                continue
            try:
                old_part.unlink()
            except PermissionError:
                print(f"warning=could_not_remove_stale_part path={old_part}")
        shutil.copy2(source, target)
        parts = sorted(parts_dir.glob("audio_part_*.wav"))
        print(f"parts_dir={parts_dir}")
        print(f"part_seconds={segment_time}")
        print(f"part_count={len(parts)}")
        print(f"{target.name}\t{target.stat().st_size}")
        return

    ffmpeg = find_ffmpeg()
    pattern = parts_dir / "audio_part_%03d.wav"

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(source),
        "-f",
        "segment",
        "-segment_time",
        str(segment_time),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(pattern),
    ]
    subprocess.run(cmd, check=True)

    parts = sorted(parts_dir.glob("audio_part_*.wav"))
    print(f"parts_dir={parts_dir}")
    print(f"part_seconds={segment_time}")
    print(f"part_count={len(parts)}")
    for part in parts:
        print(f"{part.name}\t{part.stat().st_size}")


if __name__ == "__main__":
    main()
