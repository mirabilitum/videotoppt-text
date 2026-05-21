from __future__ import annotations

import json

from common import load_config, output_dir


def main() -> None:
    load_config()

    out = output_dir()
    parts_dir = out / "transcript_parts"
    txt_parts = sorted(parts_dir.glob("transcript_part_*.txt"))
    json_parts = sorted(parts_dir.glob("transcript_part_*.json"))
    if not txt_parts:
        raise FileNotFoundError(f"No transcript parts found in {parts_dir}")

    merged_text_parts = []
    merged_json_parts = []

    for txt_path in txt_parts:
        index = int(txt_path.stem.rsplit("_", 1)[1])
        text = txt_path.read_text(encoding="utf-8").strip()
        merged_text_parts.append(f"[Part {index:03d}]\n{text}")

    for json_path in json_parts:
        merged_json_parts.append(json.loads(json_path.read_text(encoding="utf-8")))

    transcript_txt = out / "transcript.txt"
    transcript_json = out / "transcript.json"

    transcript_txt.write_text("\n\n".join(merged_text_parts) + "\n", encoding="utf-8")
    transcript_json.write_text(
        json.dumps({"parts": merged_json_parts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"merged_txt={transcript_txt}")
    print(f"merged_json={transcript_json}")
    print(f"txt_parts={len(txt_parts)}")
    print(f"json_parts={len(json_parts)}")
    print(f"chars={len(transcript_txt.read_text(encoding='utf-8'))}")


if __name__ == "__main__":
    main()
