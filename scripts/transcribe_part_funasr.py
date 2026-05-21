from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from common import load_config, output_dir


def extract_part_index(path: Path) -> int:
    return int(path.stem.rsplit("_", 1)[1])


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe one audio_part_NNN.wav.")
    parser.add_argument("part_index", type=int, help="Part index, for example 0.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite transcript_part_NNN outputs if they already exist.",
    )
    args = parser.parse_args()

    load_config()

    from funasr import AutoModel

    out = output_dir()
    parts_dir = out / "audio_parts"
    transcripts_dir = out / "transcript_parts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    part_path = parts_dir / f"audio_part_{args.part_index:03d}.wav"
    if not part_path.exists():
        raise FileNotFoundError(f"Missing audio part: {part_path}")

    txt_path = transcripts_dir / f"transcript_part_{args.part_index:03d}.txt"
    json_path = transcripts_dir / f"transcript_part_{args.part_index:03d}.json"
    if not args.overwrite and txt_path.exists() and json_path.exists():
        print(f"skip_existing={args.part_index:03d}")
        print(f"txt={txt_path}")
        print(f"json={json_path}")
        return

    def normalize_model(value: str | None) -> str | None:
        if not value:
            return value
        candidate = Path(value)
        return str(candidate) if candidate.exists() else value

    model_path = normalize_model(os.getenv("FUNASR_MODEL", "paraformer-zh"))
    vad_model_path = normalize_model(os.getenv("FUNASR_VAD_MODEL", "fsmn-vad"))
    punc_model_path = normalize_model(os.getenv("FUNASR_PUNC_MODEL", "ct-punc"))

    print(f"loading_model_for_part={args.part_index:03d}")
    model = AutoModel(
        model=model_path,
        vad_model=vad_model_path,
        punc_model=punc_model_path,
        log_level="ERROR",
        disable_update=True,
    )

    print(f"transcribing_part={part_path}")
    result = model.generate(
        input=str(part_path),
        batch_size_s=int(os.getenv("FUNASR_BATCH_SIZE_S", "300")),
        hotword=os.getenv("FUNASR_HOTWORD", ""),
    )

    text = "\n".join(r.get("text", "") for r in result)
    payload = {
        "part_index": args.part_index,
        "audio_file": str(part_path),
        "text": text,
        "result": result,
    }

    txt_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"txt={txt_path}")
    print(f"json={json_path}")
    print(f"chars={len(text)}")
    print(f"segments={len(result)}")
    print("preview=" + text[:300].replace("\n", " "))


if __name__ == "__main__":
    main()
