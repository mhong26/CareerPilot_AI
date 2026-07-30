"""match_scoring 純函式單元測試（FR-20）——零 fixture、不碰 DB / LLM。"""

from datetime import date

import pytest

from app.ai.embeddings.resume_texts import _MAX_EMBED_CHARS, build_resume_embedding_texts
from app.ai.parsers.job_schema import JobParsed
from app.ai.parsers.resume_schema import ExperienceItem, ProjectItem, ResumeParsed
from app.services.match_scoring import (
    WEIGHTS,
    apply_equivalences,
    compose_match_score,
    coverage,
    embedding_similarity,
    experience_alignment,
    extract_required_years,
    job_required_skills,
    match_skills,
    normalize_skill,
    rescale_similarity,
    resume_skill_pool,
    total_experience_years,
    years_score,
)

_E0 = [1.0, 0.0, 0.0]
_E1 = [0.0, 1.0, 0.0]
_E2 = [0.0, 0.0, 1.0]


def test_normalize_skill_edge_cases():
    """正規化：大小寫 / 空白 / 保留 c++、c#、node.js，等價字串收斂一致（FR-20）。"""
    assert normalize_skill("  Node.JS ") == normalize_skill("node.js")
    assert normalize_skill("C++") == "c++"
    assert normalize_skill("C#") == "c#"
    assert normalize_skill("CI/CD") == normalize_skill("ci cd")  # "/" 換成空白
    assert normalize_skill("  PostgreSQL  16 ") == "postgresql 16"


def test_match_skills_exact_only_no_substring():
    """Exact 層只做完全相等："Java" 不得匹配 "JavaScript"；重複 job 技能去重（FR-20）。"""
    matched, missing = match_skills(
        ["Java", "Python", "python", "FastAPI"], ["JavaScript", "Python"]
    )
    assert matched == ["Python"]
    assert missing == ["Java", "FastAPI"]


def test_resume_skill_pool_includes_project_tech():
    """技能池 = skills + projects[].tech，正規化去重保留首見原字串。"""
    parsed = ResumeParsed(
        skills=["Python", "React"],
        projects=[ProjectItem(name="p", tech=["react", "AWS"])],
    )
    assert resume_skill_pool(parsed) == ["Python", "React", "AWS"]


def test_apply_equivalences_validates_pairs():
    """通過驗證的等價 pair 轉 matched；幻覺 pair（不在 missing / 不在池）丟棄。"""
    extra, still_missing, kept = apply_equivalences(
        missing=["Go", "Kubernetes"],
        pairs=[
            ("Go", "Golang"),  # 有效：Go 在 missing、Golang 在池
            ("Rust", "Golang"),  # 幻覺：Rust 不在 missing
            ("Kubernetes", "Terraform"),  # 幻覺：Terraform 不在池
        ],
        resume_pool=["Golang", "Python"],
    )
    assert extra == ["Go"]
    assert still_missing == ["Kubernetes"]
    assert kept == [("Go", "Golang")]


def test_coverage_zero_total_is_none():
    """覆蓋率零除保護：job 無該類技能時回 None（權重歸一化，而非假中性分）。"""
    assert coverage(2, 4) == 0.5
    assert coverage(0, 0) is None


def test_total_experience_years_month_math_and_present():
    """年資：月份精確加總；"Present" 用注入的 now；解析失敗的段落跳過。"""
    now = date(2026, 1, 1)
    experience = [
        ExperienceItem(start_date="Jan 2020", end_date="Mar 2021"),  # 14 個月
        ExperienceItem(start_date="Jan 2024", end_date="Present"),  # 24 個月
        ExperienceItem(start_date="???", end_date="??"),  # 跳過
    ]
    assert total_experience_years(experience, now=now) == pytest.approx((14 + 24) / 12)


def test_total_experience_years_empty_vs_unparseable():
    """空列表 = 0.0（真訊號）；非空但全解析失敗 = None（無法判斷）。"""
    now = date(2026, 1, 1)
    assert total_experience_years([], now=now) == 0.0
    assert total_experience_years([ExperienceItem(start_date="n/a")], now=now) is None


def test_extract_required_years_lower_bound_and_max():
    """ "3+ years" → 3；"3-5 years" 取下界 3；跨行取 max；無法解析回 None。"""
    assert extract_required_years(["3+ years of backend experience"]) == 3.0
    assert extract_required_years(["3-5 years in DevOps"]) == 3.0
    assert extract_required_years(["2+ years Python", "5 years leadership"]) == 5.0
    assert extract_required_years(["Bachelor's degree required"]) is None
    assert years_score(2.0, 4.0) == 0.5
    assert years_score(10.0, 4.0) == 1.0  # 封頂
    assert years_score(None, 4.0) is None


def test_rescale_similarity_endpoints_and_midpoint():
    """Rescale [0.50, 0.85]：端點精確 0 / 1、地板以下 → 0、天花板以上 → 1、中點 0.675 → 0.5。"""
    assert rescale_similarity(0.50) == 0.0
    assert rescale_similarity(0.85) == 1.0
    assert rescale_similarity(0.2) == 0.0
    assert rescale_similarity(1.0) == 1.0
    assert rescale_similarity(0.675) == pytest.approx(0.5)


def test_experience_alignment_weighted():
    """0.25×years + 0.75×title（年資是門檻不是訊號）；單邊缺失用可用邊；都缺 None。"""
    assert experience_alignment(1.0, 0.0) == pytest.approx(0.25)
    assert experience_alignment(0.4, 0.8) == pytest.approx(0.25 * 0.4 + 0.75 * 0.8)
    assert experience_alignment(0.6, None) == pytest.approx(0.6)
    assert experience_alignment(None, 0.8) == pytest.approx(0.8)
    assert experience_alignment(None, None) is None


def test_job_required_skills_fallback():
    """required_skills 為空回退 qualifications；非空不回退；雙空回空清單。"""
    with_required = JobParsed(required_skills=["Python"], qualifications=["BSc CS"])
    assert job_required_skills(with_required) == ["Python"]
    only_quals = JobParsed(required_skills=[], qualifications=["RN license", "ICU experience"])
    assert job_required_skills(only_quals) == ["RN license", "ICU experience"]
    assert job_required_skills(JobParsed()) == []


def test_embedding_similarity_max_per_kind_then_mean():
    """正交基底手算：summary 命中 max cos=1 → 1.0；skills 全零 → 0.0；mean = 0.5。"""
    result = embedding_similarity({"summary": _E0, "skills": _E1}, [_E0, _E2])
    assert result == pytest.approx(0.5)
    assert embedding_similarity({}, [_E0]) is None
    assert embedding_similarity({"summary": _E0}, []) is None


def test_compose_match_score_full_and_renormalized():
    """加權合成：全成分手算值；缺 preferred 時權重歸一化、weights_used 總和為 1。"""
    full, weights_full = compose_match_score(
        {
            "embedding_similarity": 1.0,
            "required_coverage": 0.5,
            "preferred_coverage": 1.0,
            "experience_alignment": 0.0,
        }
    )
    assert full == pytest.approx(0.35 * 1.0 + 0.35 * 0.5 + 0.10 * 1.0)
    assert weights_full == pytest.approx(WEIGHTS)

    partial, weights_used = compose_match_score(
        {
            "embedding_similarity": 1.0,
            "required_coverage": 0.5,
            "preferred_coverage": None,
            "experience_alignment": 0.0,
        }
    )
    assert partial == pytest.approx((0.35 * 1.0 + 0.35 * 0.5) / 0.9)
    assert sum(weights_used.values()) == pytest.approx(1.0)

    empty_score, empty_weights = compose_match_score(
        {"embedding_similarity": None, "required_coverage": None}
    )
    assert (empty_score, empty_weights) == (0.0, {})


def test_build_resume_embedding_texts_skips_empty_and_truncates():
    """空 section 不產生 kind；skills 合併 project tech；超長內容截斷。"""
    parsed = ResumeParsed(
        summary="",
        skills=["Python"],
        projects=[ProjectItem(name="p", tech=["AWS"])],
        experience=[
            ExperienceItem(
                company="Acme",
                title="Engineer",
                start_date="Jan 2020",
                end_date="Present",
                bullets=["Built stuff " * 1000],  # 超長，觸發截斷
            )
        ],
    )
    texts = dict(build_resume_embedding_texts(parsed))
    assert set(texts) == {"skills", "experience"}  # summary 空 → 跳過
    assert texts["skills"] == "Skills: Python, AWS"
    assert texts["experience"].startswith("Engineer at Acme (Jan 2020 - Present)")
    assert len(texts["experience"]) <= _MAX_EMBED_CHARS
