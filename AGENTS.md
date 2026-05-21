# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python workflow for processing a Gensee recording into audio transcripts, PPT key frames, contact sheets, and an outline.

- `scripts/` contains all runnable Python entry points. Shared helpers live in `scripts/common.py`.
- `output/` is generated data: audio parts, transcript parts, merged transcripts, video parts, frames, contact sheets, and outlines.
- `models/` and `packages/` hold local FunASR/model dependencies and should be treated as heavy local assets.
- `bin/ffmpeg.exe` is the preferred local ffmpeg binary when available.
- `.env` stores runtime configuration, URLs, model paths, and API keys. Do not commit real secrets.

## Build, Test, and Development Commands

Use Python 3.13 on this machine:

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe scripts\check_env.py
```
Checks ffmpeg, Python modules, output path, and DeepSeek key presence.

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -m unittest discover -s tests -v
```
Runs the repo's unittest suite. Use this before marking changes complete.

```powershell
python scripts\download_audio.py
python scripts\split_audio.py
python scripts\transcribe_part_funasr.py 0
python scripts\merge_transcripts.py
```
Downloads audio, splits it into 15-minute chunks, transcribes one chunk, then merges transcript parts.

```powershell
python scripts\run_pipeline.py --page-url <URL> --title "课程标题" --seq 1 --output-dir D:\video\output\001_课程标题 --part-seconds 900
python scripts\extract_frames.py --source output\001_课程标题\video.ts --frames-dir output\001_课程标题\frames --threshold 0.028
python scripts\make_contact_sheet.py --frames-dir output\001_课程标题\frames --output output\001_课程标题\frames_contact.jpg
```
Downloads the full video first, splits it locally, extracts candidate PPT frames into one shared `frames/` directory, and creates a visual review sheet.

## Coding Style & Naming Conventions

Use small, script-oriented Python modules with `main()` guards. Prefer `pathlib.Path`, explicit UTF-8 file I/O, and environment loading through `load_config()`. Keep generated files named with zero-padded part indexes, such as `audio_part_004.wav`, `transcript_part_004.txt`, and `video_part_004.ts`.

## Testing Guidelines

There is a formal unittest suite under `tests/`. Validate changes with `scripts\check_env.py` and `python -m unittest discover -s tests -v`. For pipeline changes, run a narrow dry run on one or two rows, or a single-video run with `--part-seconds 900` or `0` when audio splitting needs to be skipped. Verify expected files and counts under `output/` before marking work complete.

## Commit & Pull Request Guidelines

No Git history is present, so no existing commit convention can be inferred. Use concise imperative commit messages, for example `Add frame extraction threshold option`. Pull requests should describe the processing step changed, list commands run, mention generated-output impact, and include contact-sheet screenshots when frame logic changes.

## Security & Configuration Tips

Keep `.env` local and review key/model assignments before API-backed steps. Large generated outputs, downloaded media, local models, and `__pycache__/` files should stay out of source review unless explicitly required.
For OpenCV image writes on Windows, use `scripts/common.py::write_cv_image()` instead of raw `cv2.imwrite()` when the path may contain Unicode.
Test fixtures should live under `.codex_tmp/tests`, not `D:\tmp`.
