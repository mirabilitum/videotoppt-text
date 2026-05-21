from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

try:
    from common import find_ffmpeg, load_config
except ModuleNotFoundError:  # pragma: no cover - import fallback for tests
    from .common import find_ffmpeg, load_config

try:
    from fetch_m3u8 import fetch_m3u8_urls
except ModuleNotFoundError:  # pragma: no cover - import fallback for tests
    from .fetch_m3u8 import fetch_m3u8_urls


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
PART_SECONDS_CHOICES = {0, 900, 1800, 3600}


def script_path(name: str) -> str:
    return str(SCRIPTS_DIR / name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full workflow for one video.")
    parser.add_argument("--page-url", required=True, help="Gensee playback page URL.")
    parser.add_argument("--title", required=True, help="Course title.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Per-video output directory.")
    parser.add_argument(
        "--seq",
        default=None,
        help="Optional course sequence number to store in course_info.json.",
    )
    parser.add_argument(
        "--part-seconds",
        type=int,
        default=900,
        choices=sorted(PART_SECONDS_CHOICES),
        help="Audio part length: 0, 900, 1800, or 3600 seconds.",
    )
    parser.add_argument("--skip-frames", action="store_true", help="Skip key frame extraction.")
    parser.add_argument("--resume", action="store_true", help="Skip completed steps.")
    return parser.parse_args()


def load_state(state_path: Path) -> dict[str, str]:
    if not state_path.exists():
        return {}
    payload = json.loads(state_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid pipeline state: {state_path}")
    return {str(key): str(value) for key, value in payload.items()}


def save_state(state_path: Path, state: dict[str, str]) -> None:
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_state(state_path: Path, state: dict[str, str], step: str, status: str) -> None:
    state[step] = status
    save_state(state_path, state)


def normalize_seq(seq: str | None) -> int | str | None:
    if seq is None or str(seq).strip() == "":
        return None
    value = str(seq).strip()
    try:
        return int(float(value))
    except ValueError:
        return value


def write_course_info(output_directory: Path, seq: str | None, title: str, page_url: str) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "seq": normalize_seq(seq),
        "title": title,
        "page_url": page_url,
    }
    (output_directory / "course_info.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_m3u8_urls(output_directory: Path) -> tuple[str, str]:
    path = output_directory / "m3u8_urls.json"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return str(payload["video"]).strip(), str(payload["audio"]).strip()


def child_env(output_directory: Path, video_url: str | None = None, audio_url: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["OUTPUT_DIR"] = str(output_directory)
    env["PYTHONIOENCODING"] = "utf-8"
    if video_url:
        env["VIDEO_M3U8"] = video_url
    if audio_url:
        env["AUDIO_M3U8"] = audio_url
    return env


def run_child(args: list[str], env: dict[str, str]) -> None:
    subprocess.run(args, check=True, cwd=str(ROOT), env=env)


def audio_exists(output_directory: Path) -> bool:
    path = output_directory / "audio.wav"
    return path.exists() and path.stat().st_size > 0


def split_exists(output_directory: Path) -> bool:
    return any((output_directory / "audio_parts").glob("audio_part_*.wav"))


def transcripts_complete(output_directory: Path) -> bool:
    parts_dir = output_directory / "audio_parts"
    transcripts_dir = output_directory / "transcript_parts"
    audio_parts = sorted(parts_dir.glob("audio_part_*.wav"))
    if not audio_parts:
        return False
    for part in audio_parts:
        index = part.stem.rsplit("_", 1)[1]
        if not (transcripts_dir / f"transcript_part_{index}.txt").exists():
            return False
        if not (transcripts_dir / f"transcript_part_{index}.json").exists():
            return False
    return True


def merge_exists(output_directory: Path) -> bool:
    return (output_directory / "transcript.txt").exists()


def frames_exist(output_directory: Path) -> bool:
    return any((output_directory / "frames").glob("*.jpg"))


def outline_exists(output_directory: Path) -> bool:
    return (output_directory / "outline.md").exists()


def m3u8_exists(output_directory: Path) -> bool:
    return (output_directory / "m3u8_urls.json").exists()


def audio_part_indexes(output_directory: Path) -> list[int]:
    indexes: list[int] = []
    for path in sorted((output_directory / "audio_parts").glob("audio_part_*.wav")):
        indexes.append(int(path.stem.rsplit("_", 1)[1]))
    if not indexes:
        raise FileNotFoundError(f"No audio parts found in {output_directory / 'audio_parts'}")
    return indexes


def video_part_path(output_directory: Path, part_index: int) -> Path:
    return output_directory / "video_parts" / f"video_part_{part_index:03d}.ts"


def video_full_path(output_directory: Path) -> Path:
    return output_directory / "video.ts"


def download_full_video(output_directory: Path, video_url: str, env: dict[str, str]) -> Path:
    target = video_full_path(output_directory)
    if target.exists() and target.stat().st_size > 0:
        return target

    ffmpeg = find_ffmpeg()
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-i",
            video_url,
            "-c",
            "copy",
            str(target),
        ],
        check=True,
        cwd=str(ROOT),
        env=env,
    )
    return target


def download_video_parts(
    output_directory: Path,
    video_url: str,
    part_seconds: int,
    env: dict[str, str],
) -> None:
    if part_seconds <= 0:
        raise ValueError("Video frame extraction requires --part-seconds > 0.")

    download_full_video(output_directory, video_url, env)
    audio_part_indexes(output_directory)

    parts_dir = output_directory / "video_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg()
    pattern = parts_dir / "video_part_%03d.ts"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-i",
            str(video_full_path(output_directory)),
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(part_seconds),
            "-reset_timestamps",
            "1",
            str(pattern),
        ],
        check=True,
        cwd=str(ROOT),
        env=env,
    )


def extract_frames_by_video_parts(output_directory: Path, video_url: str, part_seconds: int, env: dict[str, str]) -> None:
    download_video_parts(output_directory, video_url, part_seconds, env)

    frames_dir = output_directory / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for part_index in audio_part_indexes(output_directory):
        video_part = video_part_path(output_directory, part_index)
        if not video_part.exists():
            raise FileNotFoundError(f"Missing video part after split: {video_part}")
        run_child(
            [
                sys.executable,
                script_path("extract_frames.py"),
                "--source",
                str(video_part),
                "--frames-dir",
                str(frames_dir),
                "--time-offset-seconds",
                str(part_index * part_seconds),
            ],
            env,
        )


def run_pipeline_step(
    *,
    step: str,
    state_path: Path,
    state: dict[str, str],
    resume: bool,
    completion: Callable[[], bool],
    action: Callable[[], None],
) -> None:
    if resume and state.get(step) in {"done", "skipped"}:
        print(f"skip_step={step} status={state[step]}")
        return
    if resume and completion():
        print(f"skip_step={step} reason=completion_exists")
        mark_state(state_path, state, step, "done")
        return

    print(f"run_step={step}")
    mark_state(state_path, state, step, "running")
    try:
        action()
        if not completion():
            raise RuntimeError(f"Step {step} did not meet its completion condition.")
    except Exception:
        mark_state(state_path, state, step, "error")
        raise
    mark_state(state_path, state, step, "done")


def main() -> None:
    args = parse_args()
    load_config()

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    state_path = out / "pipeline_state.json"
    state = load_state(state_path)

    write_course_info(out, args.seq, args.title, args.page_url)
    mark_state(state_path, state, "init", "done")

    run_pipeline_step(
        step="m3u8",
        state_path=state_path,
        state=state,
        resume=args.resume,
        completion=lambda: m3u8_exists(out),
        action=lambda: fetch_m3u8_urls(args.page_url, out),
    )
    video_url, audio_url = read_m3u8_urls(out)
    env = child_env(out, video_url=video_url, audio_url=audio_url)

    run_pipeline_step(
        step="audio",
        state_path=state_path,
        state=state,
        resume=args.resume,
        completion=lambda: audio_exists(out),
        action=lambda: run_child([sys.executable, script_path("download_audio.py")], env),
    )

    run_pipeline_step(
        step="split",
        state_path=state_path,
        state=state,
        resume=args.resume,
        completion=lambda: split_exists(out),
        action=lambda: run_child(
            [
                sys.executable,
                script_path("split_audio.py"),
                "--part-seconds",
                str(args.part_seconds),
            ],
            env,
        ),
    )

    run_pipeline_step(
        step="transcribe",
        state_path=state_path,
        state=state,
        resume=args.resume,
        completion=lambda: transcripts_complete(out),
        action=lambda: run_child([sys.executable, script_path("transcribe_all_parts.py")], env),
    )

    run_pipeline_step(
        step="merge",
        state_path=state_path,
        state=state,
        resume=args.resume,
        completion=lambda: merge_exists(out),
        action=lambda: run_child([sys.executable, script_path("merge_transcripts.py")], env),
    )

    if args.skip_frames:
        frames_dir = out / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        mark_state(state_path, state, "frames", "skipped")
    else:
        run_pipeline_step(
            step="frames",
            state_path=state_path,
            state=state,
            resume=args.resume,
            completion=lambda: frames_exist(out),
            action=lambda: extract_frames_by_video_parts(out, video_url, args.part_seconds, env),
        )

    outline_args = [sys.executable, script_path("generate_outline_deepseek.py")]
    if args.resume:
        outline_args.append("--resume")
    run_pipeline_step(
        step="outline",
        state_path=state_path,
        state=state,
        resume=args.resume,
        completion=lambda: outline_exists(out),
        action=lambda: run_child(outline_args, env),
    )

    print(f"pipeline_state={state_path}")
    print(f"outline={out / 'outline.md'}")


if __name__ == "__main__":
    main()
