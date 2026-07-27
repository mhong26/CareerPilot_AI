"""Section-aware 切塊的純單元測試（FR-15）——不碰 DB / LLM。"""

from app.ai.parsers.job_schema import JobParsed
from app.ai.rag.chunking import chunk_job


def test_chunk_sections_order_and_metadata():
    """完整職缺 → 固定 section 順序、標頭正確、內容逐字保留。"""
    parsed = JobParsed(
        company="Acme",
        title="Engineer",
        location="NYC",
        work_mode="hybrid",
        responsibilities=["Do X", "Do Y"],
        required_skills=["Python", "Go"],
        preferred_skills=["Rust"],
        qualifications=["BS CS"],
        experience_requirements=["5+ years"],
    )
    drafts = chunk_job(parsed)

    assert [d.section for d in drafts] == [
        "overview",
        "responsibilities",
        "required_skills",
        "preferred_skills",
        "qualifications",
        "experience_requirements",
    ]
    overview = drafts[0].content
    assert "Company: Acme" in overview
    assert "Title: Engineer" in overview

    req = next(d for d in drafts if d.section == "required_skills")
    assert req.content.startswith("Required skills: ")
    assert "Python" in req.content and "Go" in req.content

    resp = next(d for d in drafts if d.section == "responsibilities")
    assert "- Do X" in resp.content and "- Do Y" in resp.content


def test_chunk_skips_empty_sections():
    """只有一個非空段落 → 只切一塊；全空 → 空清單。"""
    drafts = chunk_job(JobParsed(required_skills=["Python"]))
    assert len(drafts) == 1
    assert drafts[0].section == "required_skills"

    assert chunk_job(JobParsed()) == []


def test_chunk_splits_oversize_section_deterministically():
    """超長段落 → 確定性拆多塊：每塊 ≤ 上限、前綴重複、內容不遺漏、可重現。"""
    items = [f"Responsibility number {i} " + "x" * 70 for i in range(40)]
    parsed = JobParsed(responsibilities=items)
    drafts = chunk_job(parsed)

    resp_chunks = [d for d in drafts if d.section == "responsibilities"]
    assert len(resp_chunks) > 1
    assert all(len(d.content) <= 1600 for d in resp_chunks)
    assert all(d.content.startswith("Responsibilities:") for d in resp_chunks)

    joined = " ".join(d.content for d in resp_chunks)
    for item in items:
        assert item in joined

    assert chunk_job(parsed) == chunk_job(parsed)
