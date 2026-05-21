from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from common import write_cv_image


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a contact sheet for frame review.")
    parser.add_argument("--frames-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cols", type=int, default=4)
    parser.add_argument("--thumb-width", type=int, default=480)
    args = parser.parse_args()

    frames = sorted(Path(args.frames_dir).glob("*.jpg"))
    if not frames:
        raise FileNotFoundError(f"No jpg frames in {args.frames_dir}")

    thumbs = []
    for frame_path in frames:
        img = cv2.imread(str(frame_path))
        if img is None:
            continue
        scale = args.thumb_width / img.shape[1]
        thumb = cv2.resize(img, (args.thumb_width, int(img.shape[0] * scale)))
        cv2.putText(
            thumb,
            frame_path.stem,
            (10, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 0, 0),
            3,
            cv2.LINE_AA,
        )
        cv2.putText(
            thumb,
            frame_path.stem,
            (10, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        thumbs.append(thumb)

    cols = max(1, args.cols)
    rows = (len(thumbs) + cols - 1) // cols
    h, w = thumbs[0].shape[:2]
    sheet = np.full((rows * h, cols * w, 3), 245, dtype=np.uint8)

    for i, thumb in enumerate(thumbs):
        row = i // cols
        col = i % cols
        sheet[row * h : (row + 1) * h, col * w : (col + 1) * w] = thumb

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_cv_image(output, sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f"contact_sheet={output}")
    print(f"frames={len(thumbs)}")


if __name__ == "__main__":
    main()
