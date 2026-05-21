from __future__ import annotations

import importlib.util
import os
import subprocess

from common import find_ffmpeg, load_config, output_dir


def main() -> None:
    load_config()

    print(f"OUTPUT_DIR={output_dir()}")
    print(f"MODELSCOPE_CACHE={os.getenv('MODELSCOPE_CACHE', '')}")

    ffmpeg = find_ffmpeg()
    print(f"FFMPEG={ffmpeg}")
    version = subprocess.run(
        [ffmpeg, "-version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(version.stdout.splitlines()[0])

    for module in [
        "cv2",
        "requests",
        "openai",
        "funasr",
        "modelscope",
        "imageio_ffmpeg",
        "dotenv",
    ]:
        print(f"{module}={'ok' if importlib.util.find_spec(module) else 'missing'}")

    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    print(f"DEEPSEEK_API_KEY={'set' if key else 'missing'}")


if __name__ == "__main__":
    main()
