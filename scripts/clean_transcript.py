from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from openai import OpenAI

from common import ROOT, load_config, output_dir
from text_filter import (
    assert_no_alias_fragments,
    decrypt_text,
    encrypt_text,
    load_sensitive_word_map,
)


DEFAULT_PROMPT_PATH = ROOT / "prompt" / "clean_prompt.md"
MODEL_DEFAULT = "deepseek-chat"
TRUNCATED_FINISH_REASONS = {"length", "max_tokens"}


def clean_prompt_path() -> Path:
    configured = os.getenv("CLEAN_PROMPT_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_PROMPT_PATH


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def prompt_sha256() -> str:
    return file_sha256(clean_prompt_path())


def transcript_parts_dir(out: Path) -> Path:
    return out / "transcript_parts"


def raw_part_path(out: Path, index: int) -> Path:
    return transcript_parts_dir(out) / f"transcript_part_{index:03d}.txt"


def clean_part_path(out: Path, index: int) -> Path:
    return transcript_parts_dir(out) / f"transcript_part_{index:03d}_clean.txt"


def clean_meta_path(out: Path, index: int) -> Path:
    return transcript_parts_dir(out) / f"transcript_part_{index:03d}_clean.meta.json"


def read_course_context(out: Path) -> tuple[str, str]:
    path = out / "course_info.json"
    if not path.exists():
        return "未知", "未知"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid course_info.json: {path}")
    grade = str(payload.get("grade") or "未知").strip() or "未知"
    subject = str(payload.get("subject") or "未知").strip() or "未知"
    return grade, subject


def build_clean_prompt(prompt_template: str, raw_text: str, grade: str, subject: str) -> str:
    return f"""{prompt_template.strip()}

---

课程上下文：
- 年级：{grade}
- 学科：{subject}

请清洗下面这一段 ASR 转写文本。只输出清洗后的正文，不要输出 Markdown、解释或代码块。

```text
{raw_text.strip()}
```"""


def strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def call_clean_model(
    client: OpenAI,
    *,
    prompt_template: str,
    raw_text: str,
    grade: str,
    subject: str,
    max_continuations: int = 2,
) -> str:
    word_map = load_sensitive_word_map()
    user_prompt = encrypt_text(build_clean_prompt(prompt_template, raw_text, grade, subject), word_map)
    messages = [{"role": "user", "content": user_prompt}]
    chunks: list[str] = []
    max_tokens = max(8192, min(16384, int(len(raw_text) * 1.2) + 2048))

    for _ in range(max_continuations + 1):
        response = client.chat.completions.create(
            model=os.getenv("DEEPSEEK_MODEL", MODEL_DEFAULT),
            messages=messages,
            temperature=0.1,
            max_tokens=max_tokens,
        )
        choice = response.choices[0]
        content = choice.message.content or ""
        finish_reason = choice.finish_reason or ""
        chunks.append(content)
        if finish_reason not in TRUNCATED_FINISH_REASONS:
            filtered = strip_markdown_fence("\n".join(chunks))
            assert_no_alias_fragments(filtered, word_map)
            return decrypt_text(filtered, word_map).strip()

        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": "继续输出清洗后的正文，不要重复已经输出的内容。",
            }
        )

    raise RuntimeError("Clean transcript output was still truncated after continuations.")


def write_clean_outputs(
    *,
    out: Path,
    index: int,
    raw_text: str,
    clean_text: str,
    grade: str,
    subject: str,
) -> None:
    clean_path = clean_part_path(out, index)
    meta_path = clean_meta_path(out, index)
    clean_path.parent.mkdir(parents=True, exist_ok=True)

    clean_payload = clean_text.strip() + "\n"
    raw_path = raw_part_path(out, index)
    meta = {
        "index": index,
        "raw_path": str(raw_path.relative_to(out)),
        "clean_path": str(clean_path.relative_to(out)),
        "raw_size": raw_path.stat().st_size,
        "raw_sha256": sha256_text(raw_text),
        "clean_size": len(clean_payload.encode("utf-8")),
        "clean_sha256": sha256_text(clean_payload),
        "clean_prompt_sha256": prompt_sha256(),
        "grade": grade,
        "subject": subject,
    }

    clean_path.write_text(clean_payload, encoding="utf-8")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_part_with_client(out: Path, index: int, client: OpenAI) -> None:
    prompt_path = clean_prompt_path()
    if not prompt_path.exists():
        raise FileNotFoundError(f"Missing clean prompt: {prompt_path}")
    raw_path = raw_part_path(out, index)
    if not raw_path.exists():
        raise FileNotFoundError(f"Missing transcript part: {raw_path}")

    raw_text = raw_path.read_text(encoding="utf-8-sig")
    grade, subject = read_course_context(out)
    clean_text = call_clean_model(
        client,
        prompt_template=prompt_path.read_text(encoding="utf-8"),
        raw_text=raw_text,
        grade=grade,
        subject=subject,
    )
    write_clean_outputs(
        out=out,
        index=index,
        raw_text=raw_text,
        clean_text=clean_text,
        grade=grade,
        subject=subject,
    )


def clean_complete(out: Path, index: int) -> bool:
    raw_path = raw_part_path(out, index)
    clean_path = clean_part_path(out, index)
    meta_path = clean_meta_path(out, index)
    if not raw_path.exists() or not clean_path.exists() or not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return False
    raw_text = raw_path.read_text(encoding="utf-8-sig")
    clean_text = clean_path.read_text(encoding="utf-8")
    grade, subject = read_course_context(out)
    return (
        meta.get("raw_sha256") == sha256_text(raw_text)
        and meta.get("clean_sha256") == sha256_text(clean_text)
        and meta.get("clean_prompt_sha256") == prompt_sha256()
        and meta.get("grade") == grade
        and meta.get("subject") == subject
    )


def part_indexes(out: Path) -> list[int]:
    indexes = [
        int(path.stem.rsplit("_", 1)[1])
        for path in sorted(transcript_parts_dir(out).glob("transcript_part_*.txt"))
        if not path.stem.endswith("_clean")
    ]
    if not indexes:
        raise FileNotFoundError(f"No transcript_part_*.txt files found in {transcript_parts_dir(out)}")
    return indexes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean transcript parts with DeepSeek.")
    parser.add_argument("index", type=int, nargs="?", help="Optional zero-based part index to clean.")
    parser.add_argument("--overwrite", action="store_true", help="Regenerate clean output even if complete.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_config()
    out = output_dir()
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is missing. Fill it in .env first.")
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        timeout=float(os.getenv("DEEPSEEK_TIMEOUT", "180")),
        max_retries=int(os.getenv("DEEPSEEK_MAX_RETRIES", "2")),
    )

    indexes = [args.index] if args.index is not None else part_indexes(out)
    for index in indexes:
        if not args.overwrite and clean_complete(out, index):
            print(f"skip_clean_part={index:03d}")
            continue
        clean_part_with_client(out, index, client)
        print(f"clean_part={index:03d}")


if __name__ == "__main__":
    main()
