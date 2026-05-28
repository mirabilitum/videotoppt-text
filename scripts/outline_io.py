from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from outline_models import TranscriptSource


DEFAULT_PROMPT_PATH = Path("D:/video/prompt/prompt.md")


def outline_prompt_path() -> Path:
    configured = os.getenv("OUTLINE_PROMPT_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_PROMPT_PATH


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_transcript_source(out: Path, preferred: str = "auto") -> TranscriptSource:
    clean_path = out / "transcript_clean.txt"
    raw_path = out / "transcript.txt"
    if preferred not in {"auto", "clean", "raw"}:
        raise ValueError("preferred transcript source must be auto, clean, or raw.")
    if preferred == "raw":
        if raw_path.exists():
            text = raw_path.read_text(encoding="utf-8-sig")
            return TranscriptSource("raw", raw_path, text, sha256_text(text))
        raise FileNotFoundError(f"Missing transcript: {raw_path}")
    if preferred in {"auto", "clean"} and clean_path.exists():
        text = clean_path.read_text(encoding="utf-8-sig")
        return TranscriptSource("clean", clean_path, text, sha256_text(text))
    if preferred == "clean":
        raise FileNotFoundError(f"Missing clean transcript: {clean_path}")
    if raw_path.exists():
        text = raw_path.read_text(encoding="utf-8-sig")
        return TranscriptSource("raw", raw_path, text, sha256_text(text))
    raise FileNotFoundError(f"Missing transcript: {raw_path}")


def outline_source_path(out: Path) -> Path:
    return out / "outline_source.json"


def outline_policy_path(out: Path) -> Path:
    return out / "outline_policy.json"


def outline_source_payload(
    out: Path,
    source: TranscriptSource,
    prompt_path: Path,
    *,
    model: str,
    policy_path: Path | None = None,
) -> dict[str, str]:
    payload = {
        "transcript_source": source.name,
        "transcript_path": str(source.path.relative_to(out)).replace("\\", "/"),
        "transcript_sha256": source.sha256,
        "outline_prompt_path": str(prompt_path),
        "outline_prompt_sha256": file_sha256(prompt_path),
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "model": model,
    }
    if policy_path is not None and policy_path.exists():
        payload["outline_policy_path"] = str(policy_path.relative_to(out)).replace("\\", "/")
        payload["outline_policy_sha256"] = file_sha256(policy_path)
    return payload


def write_outline_source(
    out: Path,
    source: TranscriptSource,
    prompt_path: Path,
    *,
    model: str,
    policy_path: Path | None = None,
) -> None:
    outline_source_path(out).write_text(
        json.dumps(
            outline_source_payload(out, source, prompt_path, model=model, policy_path=policy_path),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def outline_inputs_match(out: Path, source: TranscriptSource, prompt_path: Path) -> bool:
    source_path = outline_source_path(out)
    if not source_path.exists():
        return False
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or not prompt_path.exists():
        return False
    if payload.get("transcript_source") != source.name:
        return False
    if payload.get("transcript_path") != str(source.path.relative_to(out)).replace("\\", "/"):
        return False
    if payload.get("transcript_sha256") != source.sha256:
        return False
    if payload.get("outline_prompt_sha256") != file_sha256(prompt_path):
        return False
    if "outline_policy_sha256" in payload:
        raw_policy_path = str(payload.get("outline_policy_path") or "")
        if not raw_policy_path:
            return False
        policy_file = out / raw_policy_path
        if not policy_file.exists() or not policy_file.is_file():
            return False
        return payload.get("outline_policy_sha256") == file_sha256(policy_file)
    return True


def outline_source_policy_matches(out: Path, policy_path: Path) -> bool:
    source_path = outline_source_path(out)
    if not source_path.exists() or not policy_path.exists():
        return False
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("outline_policy_sha256") == file_sha256(policy_path)


def outline_complete(out: Path, preferred: str = "auto") -> bool:
    outline_path = out / "outline.md"
    if not outline_path.exists():
        return False
    try:
        source = select_transcript_source(out, preferred=preferred)
        prompt_path = outline_prompt_path()
    except FileNotFoundError:
        return False
    return outline_inputs_match(out, source, prompt_path)


def read_course_title(out: Path) -> str | None:
    course_info_path = out / "course_info.json"
    if not course_info_path.exists():
        return None
    try:
        payload = json.loads(course_info_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid course_info.json: {course_info_path}") from exc
    title = payload.get("title") if isinstance(payload, dict) else None
    return str(title).strip() if title else None
