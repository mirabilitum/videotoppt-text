from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import load_config, output_dir

try:
    from clean_transcript import clean_meta_path, clean_part_path, prompt_sha256, raw_part_path, sha256_text
except ModuleNotFoundError:  # pragma: no cover - package import fallback for tests
    from .clean_transcript import clean_meta_path, clean_part_path, prompt_sha256, raw_part_path, sha256_text


def transcript_parts_dir(out: Path) -> Path:
    return out / "transcript_parts"


def raw_txt_parts(out: Path) -> list[Path]:
    parts = [
        path
        for path in sorted(transcript_parts_dir(out).glob("transcript_part_*.txt"))
        if not path.stem.endswith("_clean")
    ]
    if not parts:
        raise FileNotFoundError(f"No transcript parts found in {transcript_parts_dir(out)}")
    return parts


def raw_json_parts(out: Path) -> list[Path]:
    return sorted(transcript_parts_dir(out).glob("transcript_part_*.json"))


def part_index(path: Path) -> int:
    return int(path.stem.rsplit("_", 1)[1])


def merge_raw_parts(out: Path) -> None:
    txt_parts = raw_txt_parts(out)
    json_parts = raw_json_parts(out)
    merged_text_parts = []
    merged_json_parts = []

    for txt_path in txt_parts:
        index = part_index(txt_path)
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


def clean_manifest_path(out: Path) -> Path:
    return out / "transcript_clean_manifest.json"


def clean_transcript_path(out: Path) -> Path:
    return out / "transcript_clean.txt"


def clean_part_meta(out: Path, index: int) -> dict[str, object]:
    path = clean_meta_path(out, index)
    if not path.exists():
        raise FileNotFoundError(f"Missing clean metadata: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid clean metadata: {path}")
    return payload


def build_clean_manifest(out: Path) -> dict[str, object]:
    parts = []
    for raw_path in raw_txt_parts(out):
        index = part_index(raw_path)
        clean_path = clean_part_path(out, index)
        if not clean_path.exists():
            raise FileNotFoundError(f"Missing clean transcript part: {clean_path}")
        meta = clean_part_meta(out, index)
        raw_text = raw_path.read_text(encoding="utf-8-sig")
        clean_text = clean_path.read_text(encoding="utf-8")
        raw_hash = sha256_text(raw_text)
        clean_hash = sha256_text(clean_text)
        prompt_hash = prompt_sha256()

        if meta.get("raw_sha256") != raw_hash:
            raise RuntimeError(f"Clean metadata raw hash is stale for part {index:03d}.")
        if meta.get("clean_sha256") != clean_hash:
            raise RuntimeError(f"Clean metadata clean hash is stale for part {index:03d}.")
        if meta.get("clean_prompt_sha256") != prompt_hash:
            raise RuntimeError(f"Clean metadata prompt hash is stale for part {index:03d}.")

        parts.append(
            {
                "index": index,
                "raw_path": str(raw_path.relative_to(out)).replace("\\", "/"),
                "clean_path": str(clean_path.relative_to(out)).replace("\\", "/"),
                "raw_sha256": raw_hash,
                "clean_sha256": clean_hash,
                "clean_chars": len(clean_text),
            }
        )

    return {
        "source": "clean_parts",
        "clean_prompt_sha256": prompt_sha256(),
        "parts": parts,
    }


def merge_clean_parts(out: Path) -> None:
    manifest = build_clean_manifest(out)
    merged_text_parts = []
    for item in manifest["parts"]:
        if not isinstance(item, dict):
            raise RuntimeError("Invalid clean manifest item.")
        index = int(item["index"])
        clean_path = out / str(item["clean_path"])
        text = clean_path.read_text(encoding="utf-8").strip()
        merged_text_parts.append(f"[Part {index:03d}]\n{text}")

    clean_transcript_path(out).write_text("\n\n".join(merged_text_parts) + "\n", encoding="utf-8")
    clean_manifest_path(out).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def merge_clean_complete(out: Path) -> bool:
    transcript_path = clean_transcript_path(out)
    manifest_path = clean_manifest_path(out)
    if not transcript_path.exists() or not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        current = build_clean_manifest(out)
    except (FileNotFoundError, json.JSONDecodeError, RuntimeError):
        return False
    if manifest != current:
        return False
    expected_parts = []
    for item in current["parts"]:
        if not isinstance(item, dict):
            return False
        clean_path = out / str(item["clean_path"])
        expected_parts.append(f"[Part {int(item['index']):03d}]\n{clean_path.read_text(encoding='utf-8').strip()}")
    expected_text = "\n\n".join(expected_parts) + "\n"
    return transcript_path.read_text(encoding="utf-8") == expected_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge transcript parts.")
    parser.add_argument("--clean", action="store_true", help="Merge cleaned transcript parts only.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_config()
    out = output_dir()

    if args.clean:
        merge_clean_parts(out)
        transcript_txt = clean_transcript_path(out)
        manifest_path = clean_manifest_path(out)
        print(f"merged_clean_txt={transcript_txt}")
        print(f"merged_clean_manifest={manifest_path}")
        print(f"clean_parts={len(raw_txt_parts(out))}")
        print(f"chars={len(transcript_txt.read_text(encoding='utf-8'))}")
        return

    merge_raw_parts(out)
    transcript_txt = out / "transcript.txt"
    transcript_json = out / "transcript.json"
    print(f"merged_txt={transcript_txt}")
    print(f"merged_json={transcript_json}")
    print(f"txt_parts={len(raw_txt_parts(out))}")
    print(f"json_parts={len(raw_json_parts(out))}")
    print(f"chars={len(transcript_txt.read_text(encoding='utf-8'))}")


if __name__ == "__main__":
    main()
