from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests

try:
    from common import load_config, output_dir
except ModuleNotFoundError:  # pragma: no cover - import fallback for tests
    from .common import load_config, output_dir


VIDEO_RE = re.compile(
    r"(?:https?:)?//tencentcache\.gensee\.com/[^\s\"'<>]+record1\.m3u8"
    r"(?:\?[^\s\"'<>]+)?"
)
AUDIO_RE = re.compile(
    r"(?:https?:)?//tencentcache\.gensee\.com/[^\s\"'<>]+recordaudioonly\.m3u8"
    r"(?:\?[^\s\"'<>]+)?"
)


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    clean_url = html.unescape(url.replace("\\/", "/"))
    if clean_url.startswith("//"):
        return "https:" + clean_url
    return clean_url


def parse_m3u8_from_text(text: str) -> tuple[str | None, str | None]:
    normalized = html.unescape(text.replace("\\/", "/"))
    video_match = VIDEO_RE.search(normalized)
    audio_match = AUDIO_RE.search(normalized)
    video_url = normalize_url(video_match.group(0) if video_match else None)
    audio_url = normalize_url(audio_match.group(0) if audio_match else None)
    return video_url, audio_url


def derive_audio_from_video_url(video_url: str | None) -> str | None:
    if not video_url:
        return None
    return re.sub(r"record1\.m3u8(?:\?.*)?$", "recordaudioonly.m3u8", video_url)


def parse_hlsaudioonly_url(text: str, base_url: str) -> str | None:
    normalized = html.unescape(text.replace("\\/", "/"))
    match = re.search(r'"hlsaudioonly"\s*:\s*"([^"]+)"', normalized)
    if not match:
        return None
    return urljoin(base_url, match.group(1))


def read_cache(cache_path: Path) -> tuple[str, str] | None:
    if not cache_path.exists():
        return None
    payload = json.loads(cache_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        return None
    video_url = str(payload.get("video", "")).strip()
    audio_url = str(payload.get("audio", "")).strip()
    if video_url and audio_url:
        return video_url, audio_url
    return None


def write_cache(cache_path: Path, video_url: str, audio_url: str) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"video": video_url, "audio": audio_url}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_with_requests(page_url: str) -> tuple[str | None, str | None]:
    response = requests.get(page_url, timeout=30)
    response.raise_for_status()
    return parse_m3u8_from_text(response.text)


def fetch_with_playwright(page_url: str) -> tuple[str | None, str | None]:
    from playwright.sync_api import sync_playwright

    video_url: str | None = None
    audio_url: str | None = None
    metadata_urls: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()

            def on_request(request) -> None:
                nonlocal video_url, audio_url
                url = request.url
                if "record1.m3u8" in url:
                    video_url = url
                elif "recordaudioonly.m3u8" in url:
                    audio_url = url
                elif "record" in url and (url.endswith(".xml.js") or ".xml.js?" in url):
                    metadata_urls.append(url)

            page.on("request", on_request)
            page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            for _ in range(30):
                if video_url and (audio_url or metadata_urls):
                    break
                page.wait_for_timeout(1000)
        finally:
            browser.close()

    if not audio_url:
        audio_url = derive_audio_from_video_url(video_url)
    if not audio_url:
        for metadata_url in metadata_urls:
            try:
                response = requests.get(metadata_url, timeout=30)
                response.raise_for_status()
            except requests.RequestException:
                continue
            audio_url = parse_hlsaudioonly_url(response.text, metadata_url)
            if audio_url:
                break

    return video_url, audio_url


def fetch_m3u8_urls(page_url: str, output_directory: Path | str) -> tuple[str, str]:
    target_dir = Path(output_directory)
    cache_path = target_dir / "m3u8_urls.json"
    cached = read_cache(cache_path)
    if cached:
        return cached

    try:
        video_url, audio_url = fetch_with_requests(page_url)
    except requests.RequestException as exc:
        print(f"requests_fetch_failed={exc}")
        video_url, audio_url = None, None
    if not (video_url and audio_url):
        video_url, audio_url = fetch_with_playwright(page_url)

    if not video_url or not audio_url:
        raise RuntimeError(f"Could not find video/audio m3u8 URLs for {page_url}")

    write_cache(cache_path, video_url, audio_url)
    return video_url, audio_url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Gensee video/audio m3u8 URLs.")
    parser.add_argument("page_url", help="Gensee playback page URL.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for m3u8_urls.json cache. Defaults to OUTPUT_DIR.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_config()

    out = args.output_dir or output_dir()
    video_url, audio_url = fetch_m3u8_urls(args.page_url, out)
    print(f"video_m3u8={video_url}")
    print(f"audio_m3u8={audio_url}")


if __name__ == "__main__":
    main()
