# 2026-05-27 003 Full Run Handoff

## Requested run

- User asked to run the third Excel data row end-to-end, from playback link to final outline.
- Must run code from this worktree: `D:\tmp\video_wt\wt-clean-transcript`.
- Do not accidentally run old code from `D:\video`.
- Data/config/model assets should still come from `D:\video`.

## Third Excel row

Resolved with current worktree `scripts\run_batch.py --dry-run`:

- Excel row: `4` (row 1 is header, row 2 is first data row)
- Sequence: `003`
- Title: `一年级教材内容介绍`
- URL: `https://bjpep.gensee.com:443/webcast/site/vod/play-98f5b9c666914c84bb75c953281486a9`
- Output dir: `D:\video\output\003_一年级教材内容介绍`

## Current output state

Current files already exist under `D:\video\output\003_一年级教材内容介绍`:

- `course_info.json`
- `m3u8_urls.json`
- `audio.wav` (`214273076` bytes)
- `audio_parts\audio_part_000.wav` through `audio_part_007.wav`
- empty `transcript_parts\`
- `pipeline_state.json`

Current `pipeline_state.json`:

```json
{
  "init": "done",
  "m3u8": "done",
  "audio": "done",
  "split": "done",
  "transcribe": "error"
}
```

No Python process was left running after the interrupted run.

## What went wrong

The pipeline was started from the worktree, but `scripts/common.py` loads `.env` from `ROOT / ".env"`.

For this worktree:

- `ROOT = D:\tmp\video_wt\wt-clean-transcript`
- expected env path becomes `D:\tmp\video_wt\wt-clean-transcript\.env`
- that file does not exist

As a result, FunASR did not receive the local model paths from `D:\video\.env` and fell back to default model names, which can trigger ModelScope downloads.

Relevant local model values in `D:\video\.env`:

```text
FUNASR_MODEL=D:/video/models/models/iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch
FUNASR_VAD_MODEL=D:/video/models/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch
FUNASR_PUNC_MODEL=D:/video/models/models/iic/punc_ct-transformer_cn-en-common-vocab471067-large
DEEPSEEK_BASE_URL=https://llm-gw.shangruitong.com/v1
```

## Resume command for tomorrow

Run from `D:\tmp\video_wt\wt-clean-transcript`. This keeps the code under test in the worktree, but preloads `D:\video\.env` before `run_pipeline.py` calls `load_config()`.

```powershell
$env:PYTHONIOENCODING = 'utf-8'
@'
import runpy
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(r"D:\video\.env"), override=False)
sys.argv = [
    "run_pipeline.py",
    "--page-url",
    "https://bjpep.gensee.com:443/webcast/site/vod/play-98f5b9c666914c84bb75c953281486a9",
    "--title",
    "一年级教材内容介绍",
    "--seq",
    "3",
    "--output-dir",
    r"D:\video\output\003_一年级教材内容介绍",
    "--part-seconds",
    "900",
    "--resume",
]
runpy.run_path(str(Path(r"D:\tmp\video_wt\wt-clean-transcript\scripts\run_pipeline.py")), run_name="__main__")
'@ | C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -
```

Expected behavior with `--resume`:

- skip `m3u8` because `m3u8_urls.json` exists
- skip `audio` because `audio.wav` exists
- skip `split` because `audio_parts\audio_part_*.wav` exists
- start again at `transcribe`
- continue through merge, clean/context steps if enabled by current `run_pipeline.py`, frames, and outline

Do not use plain `python scripts\run_pipeline.py ...` from this worktree unless `D:\video\.env` has already been loaded into the process environment.
