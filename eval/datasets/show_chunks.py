"""標註輔助工具：印出情境中每個 job 經 ``chunk_job`` 切塊後的 index 與內容。

用法（repo root）::

    python -m eval.datasets.show_chunks s01          # 全部 jobs
    python -m eval.datasets.show_chunks s01 j3       # 指定 job

標 ``skill_gap.relevant_chunks`` 前先跑這個，對照 chunk_index 與內容。
"""

import sys

from app.ai.rag.chunking import chunk_job

import eval._bootstrap  # noqa: F401  # isort: split
from eval.datasets.schema import SCENARIO_DIR, load_all


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    scenario_id, job_filter = argv[0], (argv[1] if len(argv) > 1 else None)
    matches = [s for s in load_all(SCENARIO_DIR) if s.scenario_id == scenario_id]
    if not matches:
        print(f"scenario {scenario_id!r} not found under {SCENARIO_DIR}")
        return 1
    scenario = matches[0]
    for job in scenario.jobs:
        if job_filter and job.job_key != job_filter:
            continue
        marks = set(
            scenario.skill_gap.relevant_chunks if job.job_key == scenario.skill_gap.job_key else []
        )
        gap_note = "  <-- skill_gap target" if job.job_key == scenario.skill_gap.job_key else ""
        print(f"\n=== {scenario.scenario_id} / {job.job_key}: {job.parsed.title}{gap_note} ===")
        for i, draft in enumerate(chunk_job(job.parsed)):
            star = " *" if i in marks else ""
            preview = draft.content.replace("\n", " | ")
            print(f"  [{i}]{star} ({draft.section}) {preview[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
