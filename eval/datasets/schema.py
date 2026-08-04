"""Eval dataset schema 與載入器（plan.md Phase 8 第 1 項、ER-4）。

一個情境一個 JSON 檔（``scenarios/s01_<slug>.json``），欄位：

- ``resume`` / ``jobs[].parsed``：完整 ``ResumeParsed`` / ``JobParsed`` 形狀
  ——seeding 直接寫 DB 略過 LLM 解析，讓 eval 不受解析非決定性干擾。
- ``ground_truth_ranking``：graded 0-3（3=強匹配、2=好、1=邊緣、0=無關）；
  P@K / MRR 以 grade >= RELEVANT_GRADE 二值化（見 datasets/README.md）。
- ``skill_gap``：每情境恰一個 gap 目標 job；``relevant_chunks`` 以
  ``chunk_index`` 標註（chunk 級標註僅針對 skill-gap query，per plan.md）。

chunk_index 的穩定性依據：``chunk_job(parsed)`` 是 ``JobParsed`` 的純決定性
函式，production seeding 以 ``enumerate`` 賦 index——因此標註綁 parsed 內容。
``load_all`` 會重跑 chunk_job 驗證標註不越界：任何動到 parsed 或 chunking
邏輯的改動都會在測試大聲失敗，而不是默默毀掉 ground truth。
"""

import json
import re
from pathlib import Path
from typing import Literal

from app.ai.parsers.job_schema import JobParsed
from app.ai.parsers.resume_schema import ResumeParsed
from app.ai.rag.chunking import chunk_job
from pydantic import BaseModel, Field, model_validator

import eval._bootstrap  # noqa: F401  # isort: split  (backend sys.path + .env)

DATASET_DIR = Path(__file__).resolve().parent
SCENARIO_DIR = DATASET_DIR / "scenarios"

# graded 標註二值化門檻：grade >= 2 視為 relevant（README.md 記載定義與理由）。
RELEVANT_GRADE = 2

_SCENARIO_ID_RE = re.compile(r"^s\d{2}$")

# 受控 tag 詞彙：案例族統計（README census 表）依賴一致的拼字。
ALLOWED_TAGS = {
    "semantic-no-keyword-overlap",  # 含語意匹配但零關鍵字重疊的 job（grade >= 2）
    "keyword-trap",  # 含關鍵字重疊但實際無關的陷阱 job（grade 0）
    "standard",  # 無特殊構造的常規情境
    "career-changer",  # 轉職者履歷
    "junior",  # 資淺（0-2 年）
    "senior",  # 資深（7+ 年）
    "non-tech",  # 非工程職（行銷 / 設計 / 營運…）
    "cross-domain",  # 跨領域技能組合
}


class ExpectedGap(BaseModel):
    """人工標註的參考缺口——供標註者 sanity check 與 judge prompt 上下文。"""

    skill: str
    severity: Literal["high", "medium", "low"]


class SkillGapTruth(BaseModel):
    """skill-gap query 的 ground truth（chunk 級標註只在這裡）。"""

    job_key: str
    # 該 job chunk_job(parsed) 序列中「正確分析必須引用」的 chunk index（binary）。
    relevant_chunks: list[int]
    expected_gaps: list[ExpectedGap] = Field(default_factory=list)
    # 履歷明顯具備的技能：LLM 若宣稱其為缺口，即為免 judge 的標註幻覺。
    must_not_claim: list[str] = Field(default_factory=list)


class ScenarioJob(BaseModel):
    job_key: str
    raw_text: str
    parsed: JobParsed


class Scenario(BaseModel):
    scenario_id: str
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    language: str = "en"
    # WHY：這組 jobs / 標註在測什麼——彙入 datasets/README.md 的設計附錄。
    rationale: str
    resume: ResumeParsed
    jobs: list[ScenarioJob]
    ground_truth_ranking: dict[str, int]
    skill_gap: SkillGapTruth

    @model_validator(mode="after")
    def _check_consistency(self) -> "Scenario":
        if not _SCENARIO_ID_RE.match(self.scenario_id):
            raise ValueError(f"scenario_id must match sNN, got {self.scenario_id!r}")

        unknown_tags = set(self.tags) - ALLOWED_TAGS
        if unknown_tags:
            raise ValueError(f"unknown tags {sorted(unknown_tags)}; allowed: {sorted(ALLOWED_TAGS)}")

        job_keys = [job.job_key for job in self.jobs]
        if len(job_keys) != len(set(job_keys)):
            raise ValueError("duplicate job_key")

        if set(self.ground_truth_ranking) != set(job_keys):
            raise ValueError(
                "ground_truth_ranking keys must exactly cover job_keys; "
                f"missing={set(job_keys) - set(self.ground_truth_ranking)}, "
                f"extra={set(self.ground_truth_ranking) - set(job_keys)}"
            )
        for key, grade in self.ground_truth_ranking.items():
            if not 0 <= grade <= 3:
                raise ValueError(f"grade for {key} must be 0-3, got {grade}")
        if not any(g >= RELEVANT_GRADE for g in self.ground_truth_ranking.values()):
            raise ValueError("at least one job must have grade >= RELEVANT_GRADE (MRR target)")

        jobs_by_key = {job.job_key: job for job in self.jobs}
        gap_job = jobs_by_key.get(self.skill_gap.job_key)
        if gap_job is None:
            raise ValueError(f"skill_gap.job_key {self.skill_gap.job_key!r} not in jobs")

        # 每個 job 都必須切得出 chunk——否則 seeding 得到 index_status="skipped"，
        # RAG / kit 流程直接 409。
        for job in self.jobs:
            if not chunk_job(job.parsed):
                raise ValueError(f"job {job.job_key}: chunk_job(parsed) is empty")

        chunk_count = len(chunk_job(gap_job.parsed))
        annotated = self.skill_gap.relevant_chunks
        if not annotated:
            raise ValueError("skill_gap.relevant_chunks must not be empty")
        if len(annotated) != len(set(annotated)):
            raise ValueError("skill_gap.relevant_chunks contains duplicates")
        out_of_range = [i for i in annotated if not 0 <= i < chunk_count]
        if out_of_range:
            raise ValueError(
                f"skill_gap.relevant_chunks {out_of_range} out of range: "
                f"job {gap_job.job_key} has {chunk_count} chunks "
                "(run `python -m eval.datasets.show_chunks <scenario_id>` to inspect)"
            )
        return self

    @property
    def relevant_job_keys(self) -> set[str]:
        """grade >= RELEVANT_GRADE 的 job_key 集合（P@K / MRR 的 relevant set）。"""
        return {k for k, g in self.ground_truth_ranking.items() if g >= RELEVANT_GRADE}


def load_scenario(path: Path) -> Scenario:
    """載入並驗證單一情境檔；檔名前綴必須等於 scenario_id。"""
    scenario = Scenario.model_validate(json.loads(path.read_text(encoding="utf-8")))
    prefix = path.name.split("_", 1)[0]
    if prefix != scenario.scenario_id:
        raise ValueError(
            f"{path.name}: filename prefix {prefix!r} != scenario_id {scenario.scenario_id!r}"
        )
    return scenario


def load_all(scenario_dir: Path = SCENARIO_DIR) -> list[Scenario]:
    """載入全部情境（依 scenario_id 排序）；重複 id 直接失敗。"""
    scenarios = [load_scenario(p) for p in sorted(scenario_dir.glob("s*.json"))]
    ids = [s.scenario_id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate scenario_id in {scenario_dir}")
    return scenarios
