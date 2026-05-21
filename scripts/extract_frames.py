from __future__ import annotations

import argparse
import os
from pathlib import Path

import cv2

try:
    from common import load_config, output_dir, write_cv_image
except ModuleNotFoundError:  # pragma: no cover - import fallback for tests
    from .common import load_config, output_dir, write_cv_image


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract PPT-like key frames.")
    parser.add_argument(
        "--source",
        help="Video source path or URL. Defaults to output/video.ts if present, otherwise VIDEO_M3U8.",
    )
    parser.add_argument(
        "--frames-dir",
        help="Output directory for extracted frames. Defaults to output/frames.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        help="Frame difference threshold. Defaults to FRAME_DIFF_THRESHOLD.",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        help="Stop after this many seconds of video, useful for samples.",
    )
    parser.add_argument(
        "--time-offset-seconds",
        type=float,
        default=0.0,
        help="Add this offset to frame timestamps when naming output files.",
    )
    args = parser.parse_args()

    load_config()

    out = output_dir()
    frames_dir = Path(args.frames_dir) if args.frames_dir else out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    video_source = args.source or os.environ["VIDEO_M3U8"]
    local_video = out / "video.ts"
    if not args.source and local_video.exists():
        video_source = str(local_video)

    cap = cv2.VideoCapture(video_source)
    if not cap.isOpened():
        raise RuntimeError(
            "Cannot open video stream. Download video.ts first, then rerun this script."
        )

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    sample_every = max(1, int(round(fps)))
    threshold = args.threshold
    if threshold is None:
        threshold = float(os.getenv("FRAME_DIFF_THRESHOLD", "0.028"))

    prev_gray = None
    frame_count = 0
    saved_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1
        timestamp = frame_count / fps
        if args.max_seconds is not None and timestamp > args.max_seconds:
            break

        if frame_count % sample_every != 0:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_small = cv2.resize(gray, (320, 180))

        if prev_gray is not None:
            diff = cv2.absdiff(gray_small, prev_gray)
            score = diff.mean() / 255.0
            if score > threshold:
                absolute_timestamp = timestamp + args.time_offset_seconds
                minutes = int(absolute_timestamp // 60)
                seconds = int(absolute_timestamp % 60)
                filename = frames_dir / (
                    f"frame_{minutes:04d}m{seconds:02d}s_{saved_count:04d}.jpg"
                )
                write_cv_image(filename, frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
                saved_count += 1
                print(
                    f"saved={saved_count} time={minutes:02d}:{seconds:02d} "
                    f"diff={score:.3f}"
                )

        prev_gray = gray_small

    cap.release()
    print(f"source={video_source}")
    print(f"fps={fps:.3f}")
    print(f"threshold={threshold}")
    print(f"frames_dir={frames_dir}")
    print(f"saved_count={saved_count}")


if __name__ == "__main__":
    main()
