# 2026-05-26 Outline Anchor End-of-Day Handoff

**Workspace:** `D:\tmp\video_wt\wt-clean-transcript`

**Goal finished today:** Replace unstable post-hoc outline location with skeleton-emitted transcript anchors, clean obsolete fallback code, verify tests, and rerun the 002 skeleton experiment.

---

## Completed Today

- Added anchored skeleton design and plan:
  - `docs/superpowers/specs/2026-05-26-anchored-skeleton-location-design.md`
  - `docs/superpowers/plans/2026-05-26-anchored-skeleton-location.md`
- Ran CC plan/spec review:
  - Round 1: `docs/reviews/2026-05-26-anchored-skeleton-location-claude-review-1.md`
  - Round 2 confirmation: `docs/reviews/2026-05-26-anchored-skeleton-location-claude-review-2.md`
  - Log updated: `docs/review-log.md`
- Implemented skeleton anchors in `scripts/generate_outline_deepseek.py`:
  - Skeleton generation now asks each `##` heading to carry an HTML anchor comment.
  - Clean skeleton and anchored skeleton are stored separately.
  - Granularity is derived from validated anchor locations, not from average spans.
  - New generation validates `location_source == "skeleton_anchor"`.
- Added final-pass anchor repair:
  - If the final skeleton omits anchors or emits wrong anchor IDs, the script reattaches the already validated draft anchors.
  - This does not use title reverse lookup or candidate matching.
- Removed obsolete location logic:
  - Removed old post-hoc LLM location generation.
  - Removed candidate/partial/sparse/estimated granularity fallback generation.
  - Kept only compatibility parsing/validation for legacy resume files.

## Verification

Full unittest:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest discover -s tests -v
```

Result:

```text
Ran 80 tests in 1.841s
OK
```

Environment check:

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B scripts\check_env.py
```

Result summary:

```text
cv2=ok
requests=ok
openai=ok
funasr=ok
modelscope=ok
imageio_ffmpeg=ok
dotenv=ok
DEEPSEEK_API_KEY=set
```

002 anchored skeleton rerun:

```text
D:\video\output\policy_skeleton_experiment_20260526\002_policy1_skeleton2_granularity_t02_rerun_anchor1
```

Rerun result:

- `outline_skeleton_experiment.json` written.
- Skeleton run 1: `valid=True`, `chapters=13`.
- Skeleton run 2: `valid=True`, `chapters=13`.
- `outline_granularity_policy_01_run_01.json`: all 13 items have `location_source: "skeleton_anchor"`.
- `outline_granularity_policy_01_run_02.json`: all 13 items have `location_source: "skeleton_anchor"`.
- Clean skeleton files do not contain `outline-anchor`.
- Anchored skeleton files contain `outline-anchor`.

Observed model behavior:

- Both final skeleton runs omitted anchors.
- The script detected this and reattached draft anchors that had already been validated against transcript quotes.
- This is expected defensive behavior after today's change.

## Current Important Files

- Main implementation: `scripts/generate_outline_deepseek.py`
- Main tests: `tests/test_generate_outline_source.py`
- Design: `docs/superpowers/specs/2026-05-26-anchored-skeleton-location-design.md`
- Implementation plan: `docs/superpowers/plans/2026-05-26-anchored-skeleton-location.md`
- Rerun output: `D:\video\output\policy_skeleton_experiment_20260526\002_policy1_skeleton2_granularity_t02_rerun_anchor1`

## Known State To Preserve Tomorrow

- Do not reintroduce title reverse lookup for new generation.
- Do not reintroduce candidate/sparse/average fallback granularity generation.
- Keep legacy resume compatibility separate from new generation.
- Keep anchored skeleton and clean skeleton as separate artifacts.
- Keep final fill prompts using clean skeletons only; raw anchor JSON must not enter fill prompts.

