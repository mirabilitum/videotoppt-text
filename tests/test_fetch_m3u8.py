from __future__ import annotations

import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / ".codex_tmp" / "tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.fetch_m3u8 import (
    derive_audio_from_video_url,
    fetch_m3u8_urls,
    normalize_url,
    parse_hlsaudioonly_url,
    parse_m3u8_from_text,
    read_cache,
    write_cache,
)


class FetchM3u8Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.workdir = TEST_ROOT / "fetch_m3u8" / uuid.uuid4().hex
        self.workdir.mkdir(parents=True, exist_ok=True)

    def test_parse_m3u8_and_audio_fallback(self) -> None:
        text = (
            'var x = "https://tencentcache.gensee.com/live/abc/record1.m3u8?x=1";'
            'var y = "https://tencentcache.gensee.com/live/abc/recordaudioonly.m3u8?y=2";'
        )
        video_url, audio_url = parse_m3u8_from_text(text)
        self.assertEqual(video_url, "https://tencentcache.gensee.com/live/abc/record1.m3u8?x=1")
        self.assertEqual(audio_url, "https://tencentcache.gensee.com/live/abc/recordaudioonly.m3u8?y=2")
        self.assertEqual(normalize_url("//tencentcache.gensee.com/live/abc/record1.m3u8"), "https://tencentcache.gensee.com/live/abc/record1.m3u8")
        self.assertEqual(
            derive_audio_from_video_url("https://tencentcache.gensee.com/live/abc/record1.m3u8?x=1"),
            "https://tencentcache.gensee.com/live/abc/recordaudioonly.m3u8",
        )
        self.assertEqual(
            parse_hlsaudioonly_url(
                '{"hlsaudioonly":"../audio/recordaudioonly.m3u8"}',
                "https://example.com/dir/record53.xml.js",
            ),
            "https://example.com/audio/recordaudioonly.m3u8",
        )

    def test_cache_round_trip(self) -> None:
        cache_path = self.workdir / "m3u8_urls.json"
        write_cache(
            cache_path,
            "https://tencentcache.gensee.com/live/abc/record1.m3u8",
            "https://tencentcache.gensee.com/live/abc/recordaudioonly.m3u8",
        )
        self.assertEqual(
            read_cache(cache_path),
            (
                "https://tencentcache.gensee.com/live/abc/record1.m3u8",
                "https://tencentcache.gensee.com/live/abc/recordaudioonly.m3u8",
            ),
        )

    @patch("scripts.fetch_m3u8.write_cache")
    @patch("scripts.fetch_m3u8.fetch_with_playwright")
    @patch("scripts.fetch_m3u8.fetch_with_requests")
    def test_fetch_m3u8_uses_cache_then_requests_then_playwright(
        self,
        fetch_with_requests: Mock,
        fetch_with_playwright: Mock,
        write_cache_mock: Mock,
    ) -> None:
        cached_dir = self.workdir / "cached"
        cached_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cached_dir / "m3u8_urls.json"
        write_cache(
            cache_path,
            "https://tencentcache.gensee.com/live/abc/record1.m3u8",
            "https://tencentcache.gensee.com/live/abc/recordaudioonly.m3u8",
        )

        cached = fetch_m3u8_urls("https://example.com/page", cached_dir)
        self.assertTrue(cached[0].endswith("record1.m3u8"))
        self.assertEqual(fetch_with_requests.call_count, 0)
        self.assertEqual(fetch_with_playwright.call_count, 0)
        self.assertEqual(write_cache_mock.call_count, 0)

        fallback_dir = self.workdir / "fallback"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        fetch_with_requests.return_value = (None, None)
        fetch_with_playwright.return_value = (
            "https://tencentcache.gensee.com/live/abc/record1.m3u8",
            "https://tencentcache.gensee.com/live/abc/recordaudioonly.m3u8",
        )
        fetched = fetch_m3u8_urls("https://example.com/page", fallback_dir)
        self.assertTrue(fetched[0].endswith("record1.m3u8"))
        self.assertEqual(fetch_with_requests.call_count, 1)
        self.assertEqual(fetch_with_playwright.call_count, 1)
        self.assertEqual(write_cache_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
