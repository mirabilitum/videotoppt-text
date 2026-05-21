from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2

from common import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze per-second frame diff scores.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--top", type=int, default=40)
    args = parser.parse_args()

    load_config()

    cap = cv2.VideoCapture(args.source)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {args.source}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    sample_every = max(1, int(round(fps)))
    prev_gray = None
    frame_count = 0
    rows: list[dict[str, float]] = []

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1
        if frame_count % sample_every != 0:
            continue

        timestamp = frame_count / fps
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_small = cv2.resize(gray, (320, 180))

        score = 0.0
        if prev_gray is not None:
            diff = cv2.absdiff(gray_small, prev_gray)
            score = float(diff.mean() / 255.0)
            rows.append({"second": round(timestamp, 3), "score": score})

        prev_gray = gray_small

    cap.release()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["second", "score"])
        writer.writeheader()
        writer.writerows(rows)

    scores = sorted(row["score"] for row in rows)
    top_rows = sorted(rows, key=lambda row: row["score"], reverse=True)[: args.top]

    def quantile(q: float) -> float:
        if not scores:
            return 0.0
        idx = min(len(scores) - 1, max(0, round((len(scores) - 1) * q)))
        return scores[idx]

    print(f"source={args.source}")
    print(f"fps={fps:.3f}")
    print(f"samples={len(rows)}")
    print(f"csv={output}")
    for q in [0.50, 0.75, 0.90, 0.95, 0.98, 0.99]:
        print(f"p{int(q * 100)}={quantile(q):.4f}")
    print("top_scores:")
    for row in top_rows:
        print(f"{row['second']:8.3f}\t{row['score']:.4f}")


if __name__ == "__main__":
    main()
