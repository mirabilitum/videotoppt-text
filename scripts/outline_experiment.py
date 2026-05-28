from __future__ import annotations

import json
from pathlib import Path

from openai import OpenAI

from outline_granularity import format_granularity_plan
from outline_io import file_sha256, outline_policy_path
from outline_llm import (
    call_outline_policy_pass,
    generate_skeleton_with_granularity,
    merge_outline_policy_runs,
)
from outline_models import OutlinePolicy, PolicyMergeResult, SkeletonRepairError
from outline_policy import format_outline_policy
from outline_skeleton import (
    parse_chapters,
    validate_skeleton_matches_granularity,
    validate_skeleton_matches_policy,
)


def write_skeleton_experiment_manifest(
    out: Path,
    records: list[dict[str, object]],
    *,
    policy_records: list[dict[str, object]] | None = None,
    policy_merge: PolicyMergeResult | None = None,
    canonical_policy_path: Path | None = None,
) -> None:
    payload: dict[str, object] = {"runs": records}
    if policy_records is not None:
        payload["policy_runs"] = policy_records
    if policy_merge is not None:
        payload["policy_merge_reason"] = policy_merge.reason
        payload["policy_merge_source_run"] = policy_merge.source_run
        payload["policy_merge_trace_count"] = len(policy_merge.policy.get("merge_trace", []))
        payload["policy_dropped_or_merged_count"] = len(
            policy_merge.policy.get("dropped_or_merged_items", [])
        )
    if canonical_policy_path is not None:
        payload["canonical_policy_path"] = canonical_policy_path.name
        payload["canonical_policy_sha256"] = file_sha256(canonical_policy_path)
    (out / "outline_skeleton_experiment.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

def run_skeleton_only_experiment(
    *,
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    course_title: str | None,
    out: Path,
    policy_runs: int,
    skeleton_runs: int,
) -> None:
    if policy_runs < 1:
        raise ValueError("--policy-runs must be at least 1.")
    if skeleton_runs < 1:
        raise ValueError("--skeleton-runs must be at least 1.")

    char_count = len(transcript)
    records: list[dict[str, object]] = []
    policy_records: list[dict[str, object]] = []
    policies: list[OutlinePolicy] = []
    first_skeleton_path: Path | None = None

    for policy_index in range(1, policy_runs + 1):
        print(f"Pass 0: generating outline policy {policy_index}/{policy_runs}...")
        policy = call_outline_policy_pass(client, prompt_template, transcript)
        policies.append(policy)
        policy_path = out / f"outline_policy_run_{policy_index:02d}.json"
        policy_path.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        policy_records.append(
            {
                "policy_run": policy_index,
                "policy_path": policy_path.name,
                "policy_sha256": file_sha256(policy_path),
                "top_level_count": len(policy.get("top_level_items", [])),
            }
        )

    policy_merge = merge_outline_policy_runs(client, prompt_template, transcript, policies)
    policy = policy_merge.policy
    canonical_policy_path = out / "outline_policy_canonical.json"
    canonical_policy_path.write_text(format_outline_policy(policy) + "\n", encoding="utf-8")
    outline_policy_path(out).write_text(
        canonical_policy_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    for skeleton_index in range(1, skeleton_runs + 1):
        print(
            "Pass 1: generating outline skeleton "
            f"canonical_policy skeleton={skeleton_index}/{skeleton_runs}..."
        )
        try:
            skeleton_result = generate_skeleton_with_granularity(
                client,
                prompt_template,
                transcript,
                course_title,
                policy,
                char_count,
            )
        except RuntimeError as exc:
            validation_error = str(exc)
            retry_report = exc.retry_report if isinstance(exc, SkeletonRepairError) else {}
            record_status = exc.status if isinstance(exc, SkeletonRepairError) else "INVALID"
            print(
                "Pass 1: skeleton generation failed "
                f"skeleton={skeleton_index}: {validation_error}"
            )
            records.append(
                {
                    "skeleton_run": skeleton_index,
                    "canonical_policy_path": canonical_policy_path.name,
                    "canonical_policy_sha256": file_sha256(canonical_policy_path),
                    "top_level_count": len(policy.get("top_level_items", [])),
                    "chapter_count": 0,
                    "valid": False,
                    "validation_error": validation_error,
                    "skeleton_retry_status": record_status,
                    "anchor_repair_count": retry_report.get("anchor_repair_count", 0),
                    "granularity_repair_count": retry_report.get("granularity_repair_count", 0),
                    "anchor_error": retry_report.get(
                        "anchor_error",
                        validation_error if record_status == "ANCHOR_FAIL" else "",
                    ),
                    "granularity_repair_chapters": retry_report.get(
                        "granularity_repair_chapters",
                        [],
                    ),
                }
            )
            continue
        skeleton = skeleton_result.skeleton
        granularity_plan = skeleton_result.granularity_plan
        granularity_path = out / f"outline_granularity_run_{skeleton_index:02d}.json"
        granularity_path.write_text(
            format_granularity_plan(granularity_plan) + "\n",
            encoding="utf-8",
        )
        _, chapters = parse_chapters(skeleton)
        validation_error = ""
        try:
            validate_skeleton_matches_policy(chapters, policy)
            validate_skeleton_matches_granularity(chapters, granularity_plan)
        except RuntimeError as exc:
            validation_error = str(exc)
            print(
                "Pass 1: skeleton did not match canonical policy "
                f"skeleton={skeleton_index}: {validation_error}"
            )
        skeleton_path = out / f"outline_skeleton_run_{skeleton_index:02d}.md"
        anchored_skeleton_path = out / f"outline_skeleton_anchored_run_{skeleton_index:02d}.md"
        anchored_skeleton_path.write_text(
            skeleton_result.anchored_skeleton + "\n",
            encoding="utf-8",
        )
        skeleton_path.write_text(skeleton + "\n", encoding="utf-8")
        if first_skeleton_path is None and not validation_error:
            first_skeleton_path = skeleton_path
        retry_report = skeleton_result.retry_report or {}
        records.append(
            {
                "skeleton_run": skeleton_index,
                "canonical_policy_path": canonical_policy_path.name,
                "canonical_policy_sha256": file_sha256(canonical_policy_path),
                "granularity_path": granularity_path.name,
                "granularity_sha256": file_sha256(granularity_path),
                "anchored_skeleton_path": anchored_skeleton_path.name,
                "anchored_skeleton_sha256": file_sha256(anchored_skeleton_path),
                "skeleton_path": skeleton_path.name,
                "skeleton_sha256": file_sha256(skeleton_path),
                "top_level_count": len(policy.get("top_level_items", [])),
                "chapter_count": len(chapters),
                "valid": not validation_error,
                "validation_error": validation_error,
                "skeleton_retry_status": retry_report.get("status", "valid"),
                "anchor_repair_count": retry_report.get("anchor_repair_count", 0),
                "granularity_repair_count": retry_report.get("granularity_repair_count", 0),
                "anchor_error": retry_report.get("anchor_error", ""),
                "granularity_repair_chapters": retry_report.get(
                    "granularity_repair_chapters",
                    [],
                ),
            }
        )

    if first_skeleton_path is not None:
        (out / "outline_skeleton.md").write_text(
            first_skeleton_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    write_skeleton_experiment_manifest(
        out,
        records,
        policy_records=policy_records,
        policy_merge=policy_merge,
        canonical_policy_path=canonical_policy_path,
    )
    print(f"outline_skeleton_experiment.json={out / 'outline_skeleton_experiment.json'}")
