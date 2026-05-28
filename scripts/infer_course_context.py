from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
import re
from pathlib import Path

from openai import OpenAI

from common import ROOT, load_config, output_dir
from text_filter import decrypt_text, encrypt_text, load_sensitive_word_map


DEFAULT_PROMPT_PATH = ROOT / "prompt" / "course_context_prompt.md"
MODEL_DEFAULT = "deepseek-chat"
VALID_CONTEXT_SOURCES = {"transcript_head_tail", "cli_override", "skipped"}


@dataclass(frozen=True)
class TranscriptSample:
    text: str
    head: str
    tail: str
    head_chars: int
    tail_chars: int


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_part_markers(text: str) -> str:
    return re.sub(r"\[Part \d+\]\s*", "", text)


def sample_transcript(text: str, sample_chars: int = 1000) -> TranscriptSample:
    stripped = strip_part_markers(text).strip()
    if len(stripped) <= sample_chars * 2:
        return TranscriptSample(
            text=stripped,
            head=stripped,
            tail="",
            head_chars=len(stripped),
            tail_chars=0,
        )
    head = stripped[:sample_chars]
    tail = stripped[-sample_chars:]
    return TranscriptSample(
        text=f"{head}\n\n---TAIL---\n\n{tail}",
        head=head,
        tail=tail,
        head_chars=len(head),
        tail_chars=len(tail),
    )


def validate_grade_subject_args(grade: str | None, subject: str | None) -> None:
    if bool((grade or "").strip()) != bool((subject or "").strip()):
        raise ValueError("--grade and --subject must be provided together.")


def read_course_info(out: Path) -> dict[str, object]:
    path = out / "course_info.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid course_info.json: {path}")
    return payload


def write_course_info(out: Path, payload: dict[str, object]) -> None:
    (out / "course_info.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def transcript_path(out: Path) -> Path:
    return out / "transcript.txt"


def transcript_sha256(out: Path) -> str:
    return sha256_text(transcript_path(out).read_text(encoding="utf-8-sig"))


def write_context_override(out: Path, *, grade: str, subject: str) -> None:
    text_hash = transcript_sha256(out)
    payload = read_course_info(out)
    payload["grade"] = grade
    payload["subject"] = subject
    payload["context_inference"] = {
        "source": "cli_override",
        "transcript_sha256": text_hash,
    }
    write_course_info(out, payload)


def write_context_skipped(out: Path) -> None:
    text_hash = transcript_sha256(out)
    payload = read_course_info(out)
    payload["grade"] = "未知"
    payload["subject"] = "未知"
    payload["context_inference"] = {
        "source": "skipped",
        "transcript_sha256": text_hash,
    }
    write_course_info(out, payload)


def infer_context_complete(out: Path) -> bool:
    path = out / "course_info.json"
    transcript = transcript_path(out)
    if not path.exists() or not transcript.exists():
        return False
    try:
        payload = read_course_info(out)
    except (json.JSONDecodeError, RuntimeError):
        return False

    grade = str(payload.get("grade") or "").strip()
    subject = str(payload.get("subject") or "").strip()
    context = payload.get("context_inference")
    if not grade or not subject or not isinstance(context, dict):
        return False
    source = str(context.get("source") or "")
    if source not in VALID_CONTEXT_SOURCES:
        return False
    if str(context.get("transcript_sha256") or "") != transcript_sha256(out):
        return False
    if source == "transcript_head_tail":
        required = ("confidence", "evidence_location", "evidence_quote")
        return all(key in context for key in required)
    return True


def context_prompt_path() -> Path:
    configured = os.getenv("COURSE_CONTEXT_PROMPT_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_PROMPT_PATH


def parse_model_json(text: str) -> dict[str, object]:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if match:
        stripped = match.group(1).strip()
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise RuntimeError("Course context response must be a JSON object.")
    return payload


def normalize_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def call_context_model(client: OpenAI, prompt_template: str, sample: TranscriptSample) -> dict[str, object]:
    word_map = load_sensitive_word_map()
    user_prompt = f"{prompt_template.strip()}\n\n```text\n{sample.text}\n```"
    response = client.chat.completions.create(
        model=os.getenv("DEEPSEEK_MODEL", MODEL_DEFAULT),
        messages=[
            {
                "role": "system",
                "content": "你只输出课程年级和学科识别 JSON。",
            },
            {
                "role": "user",
                "content": encrypt_text(user_prompt, word_map),
            },
        ],
        temperature=0.1,
        max_tokens=2048,
    )
    content = response.choices[0].message.content or ""
    return parse_model_json(decrypt_text(content, word_map))


def infer_context_with_client(out: Path, client: OpenAI) -> None:
    prompt_path = context_prompt_path()
    if not prompt_path.exists():
        raise FileNotFoundError(f"Missing course context prompt: {prompt_path}")
    raw_transcript = transcript_path(out).read_text(encoding="utf-8-sig")
    sample = sample_transcript(raw_transcript)
    result = call_context_model(client, prompt_path.read_text(encoding="utf-8"), sample)

    grade = str(result.get("grade") or "未知").strip() or "未知"
    subject = str(result.get("subject") or "未知").strip() or "未知"
    evidence_quote = str(result.get("evidence_quote") or "").strip()
    if evidence_quote and evidence_quote not in sample.text:
        raise RuntimeError("evidence_quote must come from the transcript sample.")

    payload = read_course_info(out)
    payload["grade"] = grade
    payload["subject"] = subject
    payload["context_inference"] = {
        "source": "transcript_head_tail",
        "head_chars": sample.head_chars,
        "tail_chars": sample.tail_chars,
        "transcript_sha256": sha256_text(raw_transcript),
        "confidence": normalize_confidence(result.get("confidence")),
        "evidence_location": str(result.get("evidence_location") or "none").strip() or "none",
        "evidence_quote": evidence_quote,
    }
    write_course_info(out, payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Infer course grade and subject from transcript.txt.")
    parser.add_argument("--grade", help="Manual grade override. Must be paired with --subject.")
    parser.add_argument("--subject", help="Manual subject override. Must be paired with --grade.")
    parser.add_argument("--skip-context-infer", action="store_true", help="Write unknown context without LLM.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_grade_subject_args(args.grade, args.subject)
    load_config()
    out = output_dir()
    if not transcript_path(out).exists():
        raise FileNotFoundError(f"Missing transcript: {transcript_path(out)}")

    if args.grade and args.subject:
        write_context_override(out, grade=args.grade.strip(), subject=args.subject.strip())
        print(f"course_info={out / 'course_info.json'}")
        print("context_source=cli_override")
        return

    if args.skip_context_infer:
        write_context_skipped(out)
        print(f"course_info={out / 'course_info.json'}")
        print("context_source=skipped")
        return

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is missing. Fill it in .env first.")
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        timeout=float(os.getenv("DEEPSEEK_TIMEOUT", "180")),
        max_retries=int(os.getenv("DEEPSEEK_MAX_RETRIES", "2")),
    )
    infer_context_with_client(out, client)
    print(f"course_info={out / 'course_info.json'}")
    print("context_source=transcript_head_tail")


if __name__ == "__main__":
    main()
