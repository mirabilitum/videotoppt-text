from __future__ import annotations

import argparse
import os
import subprocess

from common import find_ffmpeg, load_config, output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Download one video segment.")
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--part-index", type=int, default=0)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    load_config()

    out = output_dir()
    parts_dir = out / "video_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    target = parts_dir / (args.output or f"video_part_{args.part_index:03d}.ts")
    ffmpeg = find_ffmpeg()

    start = args.part_index * args.seconds
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-ss",
        str(start),
        "-t",
        str(args.seconds),
        "-i",
        os.environ["VIDEO_M3U8"],
        "-c",
        "copy",
        str(target),
    ]
    subprocess.run(cmd, check=True)

    print(f"sample={target}")
    print(f"part_index={args.part_index}")
    print(f"start={start}")
    print(f"seconds={args.seconds}")
    print(f"size={target.stat().st_size}")


if __name__ == "__main__":
    main()
