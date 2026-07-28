"""履歷 embedding 文字建構（Phase 5 Step 0，FR-20 embedding similarity 查詢側）。

與 job 端的 ``ai/rag/chunking.py`` 對應：把結構化履歷組成最多三段可向量化
文字（summary / skills / experience）。前綴風格（"Skills: ..."）與 job chunk
一致，讓查詢側與文件側落在相近的措辭空間。純函式，無 DB / LLM 依賴。
"""

from app.ai.parsers.resume_schema import ResumeParsed

# 單段上限（約 1500 tokens）：防異常長履歷爆 embedding 輸入；截斷不影響儲存原文。
_MAX_EMBED_CHARS = 6000


def build_resume_embedding_texts(parsed: ResumeParsed) -> list[tuple[str, str]]:
    """組出 ``(kind, text)`` 清單；空 section 跳過，每段截斷至 ``_MAX_EMBED_CHARS``。"""
    texts: list[tuple[str, str]] = []

    summary = parsed.summary.strip()
    if summary:
        texts.append(("summary", f"Summary: {summary}"[:_MAX_EMBED_CHARS]))

    # skills + project tech 合併（小寫去重、保留首見原字串），與 match_scoring
    # 的 resume_skill_pool 同一構成邏輯。
    merged: list[str] = []
    seen: set[str] = set()
    for skill in [
        *(s.strip() for s in parsed.skills),
        *(t.strip() for p in parsed.projects for t in p.tech),
    ]:
        if skill and skill.lower() not in seen:
            seen.add(skill.lower())
            merged.append(skill)
    if merged:
        texts.append(("skills", ("Skills: " + ", ".join(merged))[:_MAX_EMBED_CHARS]))

    blocks: list[str] = []
    for item in parsed.experience:
        header = " at ".join(p for p in (item.title.strip(), item.company.strip()) if p)
        period = f"{item.start_date.strip()} - {item.end_date.strip()}".strip(" -")
        if period:
            header = f"{header} ({period})" if header else period
        lines = [header] if header else []
        lines.extend(f"- {b.strip()}" for b in item.bullets if b.strip())
        if lines:
            blocks.append("\n".join(lines))
    if blocks:
        texts.append(("experience", "\n\n".join(blocks)[:_MAX_EMBED_CHARS]))

    return texts
