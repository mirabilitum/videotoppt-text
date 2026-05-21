from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def load_config() -> None:
    load_dotenv(ENV_PATH, override=False)

    bin_dir = ROOT / "bin"
    if bin_dir.exists():
        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{current_path}"

    model_cache = os.getenv("MODELSCOPE_CACHE")
    if model_cache:
        Path(model_cache).mkdir(parents=True, exist_ok=True)
        os.environ["MODELSCOPE_CACHE"] = model_cache


def output_dir() -> Path:
    out = Path(os.getenv("OUTPUT_DIR", "D:/video/output"))
    out.mkdir(parents=True, exist_ok=True)
    return out


def find_ffmpeg() -> str:
    configured = os.getenv("FFMPEG_PATH", "").strip()
    if configured and Path(configured).exists():
        return configured

    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg and Path(ffmpeg).exists():
            return ffmpeg
    except Exception:
        pass

    raise FileNotFoundError(
        "No ffmpeg found. Set FFMPEG_PATH in .env or install imageio-ffmpeg."
    )


def write_cv_image(path: Path, image, params: list[int] | None = None) -> None:
    """Write an OpenCV image through Python I/O so Windows Unicode paths work."""
    import cv2

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    extension = output_path.suffix or ".jpg"
    ok, encoded = cv2.imencode(extension, image, params or [])
    if not ok:
        raise RuntimeError(f"Could not encode image for {output_path}")
    output_path.write_bytes(encoded.tobytes())
