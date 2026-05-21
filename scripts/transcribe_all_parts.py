from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from common import load_config, output_dir


def extract_part_index(path: Path) -> int:
    return int(path.stem.rsplit("_", 1)[1])


def normalize_model(value: str | None) -> str | None:
    if not value:
        return value
    candidate = Path(value)
    return str(candidate) if candidate.exists() else value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe all audio_part_NNN.wav files with one FunASR load."
    )
    parser.add_argument(
        "--parts-dir",
        type=Path,
        help="Directory containing audio_part_NNN.wav files. Defaults to OUTPUT_DIR/audio_parts.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help=(
            "Directory for transcript_part_NNN outputs. "
            "Defaults to OUTPUT_DIR/transcript_parts."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing transcript_part_NNN outputs.",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="Start from this 0-based part index for resumable runs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_config()

    out = output_dir()
    parts_dir = args.parts_dir or out / "audio_parts"
    transcripts_dir = args.out_dir or out / "transcript_parts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    if args.start < 0:
        raise ValueError("--start must be 0 or a positive integer.")

    parts = [
        part
        for part in sorted(parts_dir.glob("audio_part_*.wav"), key=extract_part_index)
        if extract_part_index(part) >= args.start
    ]
    if not parts:
        raise FileNotFoundError(f"No audio parts found in {parts_dir} from start={args.start}")

    pending: list[tuple[int, Path, Path, Path]] = []
    for part_path in parts:
        part_index = extract_part_index(part_path)
        txt_path = transcripts_dir / f"transcript_part_{part_index:03d}.txt"
        json_path = transcripts_dir / f"transcript_part_{part_index:03d}.json"
        if not args.overwrite and txt_path.exists() and json_path.exists():
            print(f"skip_existing={part_index:03d}")
            print(f"txt={txt_path}")
            print(f"json={json_path}")
            continue
        pending.append((part_index, part_path, txt_path, json_path))

    if not pending:
        print("all_parts_skipped=true")
        return

    from funasr import AutoModel

    model_path = normalize_model(os.getenv("FUNASR_MODEL", "paraformer-zh"))
    vad_model_path = normalize_model(os.getenv("FUNASR_VAD_MODEL", "fsmn-vad"))
    punc_model_path = normalize_model(os.getenv("FUNASR_PUNC_MODEL", "ct-punc"))

    print("loading_model_once")
    model = AutoModel(
        model=model_path,
        vad_model=vad_model_path,
        punc_model=punc_model_path,
        log_level="ERROR",
        disable_update=True,
    )

    for part_index, part_path, txt_path, json_path in pending:
        print(f"transcribing_part={part_path}")
        result = model.generate(
            input=str(part_path),
            batch_size_s=int(os.getenv("FUNASR_BATCH_SIZE_S", "300")),
            hotword=os.getenv("FUNASR_HOTWORD", ""),
        )

        text = "\n".join(r.get("text", "") for r in result)
        payload = {
            "part_index": part_index,
            "audio_file": str(part_path),
            "text": text,
            "result": result,
        }

        txt_path.write_text(text, encoding="utf-8")
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"txt={txt_path}")
        print(f"json={json_path}")
        print(f"chars={len(text)}")
        print(f"segments={len(result)}")
        print("preview=" + text[:300].replace("\n", " "))


if __name__ == "__main__":
    main()
