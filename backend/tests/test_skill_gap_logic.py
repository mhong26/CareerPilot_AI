"""Skill gap 純函式單元測試——零 fixture、不碰 DB / LLM / cross-encoder 模型。

覆蓋 rerank 排序、citation 驗證（FR-28 幻覺防護）與 prompt 組裝。
"""

import uuid

import pytest

from app.ai.parsers.skill_gap_schema import SkillGapAnalysis, SkillGapItem
from app.ai.prompts.skill_gap import build_skill_gap_prompt
from app.ai.rag.rerank import rerank_order
from app.services.skill_gap_service import normalize_severity, validate_analysis


def test_rerank_order_desc_and_stable():
    """分數 desc 排序；同分保留原（向量）序；長度不符丟 ValueError。"""
    assert rerank_order([0.1, 0.9, 0.5], 3) == [1, 2, 0]
    assert rerank_order([1.0, 1.0, 0.5], 3) == [0, 1, 2]  # 穩定排序：同分不翻轉
    with pytest.raises(ValueError):
        rerank_order([0.1, 0.9], 3)


def test_normalize_severity():
    """strip/lower 後只接受 high/medium/low，其餘（含幻覺值）一律 medium。"""
    assert normalize_severity("HIGH") == "high"
    assert normalize_severity(" Medium ") == "medium"
    assert normalize_severity("low") == "low"
    assert normalize_severity("critical") == "medium"
    assert normalize_severity("") == "medium"


def test_validate_analysis_citation_guard():
    """編號 → UUID 映射；越界剔除、去重保序、零證據 gap 丟棄並計數（FR-28）。"""
    ranked = [uuid.uuid4(), uuid.uuid4()]
    analysis = SkillGapAnalysis(
        gaps=[
            SkillGapItem(skill="Go", severity="HIGH", evidence_chunk_numbers=[1]),
            SkillGapItem(skill="Ghost", evidence_chunk_numbers=[99, 0, -1]),
            SkillGapItem(skill="Rust", severity="low", evidence_chunk_numbers=[2, 2, 1, 99]),
        ],
        overall_summary="ok",
    )

    payload = validate_analysis(analysis, ranked)

    assert [g["skill"] for g in payload["gaps"]] == ["Go", "Rust"]
    assert payload["dropped_gap_count"] == 1
    assert payload["overall_summary"] == "ok"
    assert payload["gaps"][0]["severity"] == "high"  # 正規化
    assert payload["gaps"][0]["evidence_chunk_ids"] == [str(ranked[0])]
    # 去重保序：2,2,1 → [ranked[1], ranked[0]]；99 剔除。
    assert payload["gaps"][1]["evidence_chunk_ids"] == [str(ranked[1]), str(ranked[0])]


def test_validate_analysis_empty_gaps():
    """LLM 回空 gaps（無缺口）是合法結果，不觸發丟棄計數。"""
    payload = validate_analysis(SkillGapAnalysis(overall_summary="all good"), [uuid.uuid4()])
    assert payload["gaps"] == []
    assert payload["dropped_gap_count"] == 0


def test_build_skill_gap_prompt_format():
    """證據以 [n] (section) 編號呈現、超長 chunk 截斷、hint block 僅在有 hint 時出現。"""
    long_content = "x" * 2000
    prompt = build_skill_gap_prompt(
        job_title="Backend Engineer",
        job_company="Acme",
        chunks=[("required_skills", "Required skills: Python, Go"), ("overview", long_content)],
        resume_skills=["Python"],
        resume_summary="Backend engineer.",
        resume_years=3.0,
        prior_missing_skills=[],
    )
    assert "[1] (required_skills) Required skills: Python, Go" in prompt
    assert "[2] (overview) " + "x" * 1600 in prompt
    assert "x" * 1601 not in prompt  # _MAX_CHUNK_CHARS 截斷
    assert "Total experience: 3.0 years" in prompt
    assert "previous deterministic match" not in prompt  # 無 hint → 無 hint block

    hinted = build_skill_gap_prompt(
        job_title="",
        job_company="",
        chunks=[("required_skills", "Required skills: Go")],
        resume_skills=[],
        resume_summary="",
        resume_years=None,
        prior_missing_skills=["Go"],
    )
    assert "previous deterministic match" in hinted
    assert "(unknown title)" in hinted
    assert "Total experience: unknown" in hinted
