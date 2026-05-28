from __future__ import annotations

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SENSITIVE_WORD_LIST = ROOT / "config" / "sensitive_words.json"


def sensitive_word_list_path() -> Path:
    configured = os.getenv("SENSITIVE_WORD_LIST", "").strip()
    if not configured:
        return DEFAULT_SENSITIVE_WORD_LIST
    path = Path(configured)
    return path if path.is_absolute() else ROOT / path


def load_sensitive_word_map(path: Path | None = None) -> dict[str, str]:
    word_list_path = path or sensitive_word_list_path()
    if not word_list_path.exists():
        return {}

    payload = json.loads(word_list_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Sensitive word list must be a JSON object: {word_list_path}")

    word_map: dict[str, str] = {}
    aliases: set[str] = set()
    for raw_word, raw_alias in payload.items():
        word = str(raw_word).strip()
        alias = str(raw_alias).strip()
        if not word or not alias:
            continue
        if alias in aliases:
            raise RuntimeError(f"Duplicate sensitive word alias {alias!r} in {word_list_path}")
        aliases.add(alias)
        word_map[word] = alias
    return word_map


def encrypt_text(text: str, word_map: dict[str, str]) -> str:
    encrypted = text
    for word, alias in sorted(word_map.items(), key=lambda item: len(item[0]), reverse=True):
        encrypted = encrypted.replace(word, alias)
    return encrypted


def decrypt_text(text: str, word_map: dict[str, str]) -> str:
    decrypted = text
    for word, alias in sorted(word_map.items(), key=lambda item: len(item[1]), reverse=True):
        decrypted = decrypted.replace(alias, word)
    return decrypted


def alias_fragments(alias: str) -> set[str]:
    min_length = 4
    if len(alias) <= min_length:
        return set()

    fragments: set[str] = set()
    for length in range(min_length, len(alias)):
        fragments.add(alias[:length])
        fragments.add(alias[-length:])

    core = alias.strip("_")
    if core != alias and len(core) >= min_length:
        fragments.add(core)
    return fragments


def assert_no_alias_fragments(text: str, word_map: dict[str, str]) -> None:
    text_without_full_aliases = text
    for full_alias in word_map.values():
        text_without_full_aliases = text_without_full_aliases.replace(full_alias, "")

    for alias in word_map.values():
        for fragment in alias_fragments(alias):
            if fragment in text_without_full_aliases:
                raise RuntimeError(
                    "Model output appears to contain a modified sensitive-word alias "
                    f"fragment: {fragment!r}"
                )


def adjust_span_to_alias_boundary(
    text: str,
    start: int,
    end: int,
    aliases: set[str],
) -> tuple[int, int]:
    adjusted_start = start
    adjusted_end = end

    for alias in aliases:
        offset = 0
        while True:
            index = text.find(alias, offset)
            if index < 0:
                break
            alias_end = index + len(alias)
            if index < adjusted_start < alias_end:
                adjusted_start = index
            if index < adjusted_end < alias_end:
                adjusted_end = alias_end
            if adjusted_start <= index < adjusted_end < alias_end:
                adjusted_end = alias_end
            if index < adjusted_start < alias_end <= adjusted_end:
                adjusted_start = index
            offset = alias_end

    return adjusted_start, adjusted_end
