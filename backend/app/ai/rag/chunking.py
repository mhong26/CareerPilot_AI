"""Section-aware 職缺切塊（FR-15）。

把結構化 ``JobParsed`` 切成一段段 ``ChunkDraft``，每段：
- 只含單一語意段落（overview / responsibilities / required_skills ...），語意純、
  embedding 品質好，且 Phase 6 引用出處時可讀。
- 內文帶段落前綴（"Required skills: ..."），使每塊文字自我說明。

純函式、不碰 DB / LLM：相同輸入必得相同輸出，方便單元測試。超長段落以字元近似
（約 4 字元 = 1 token）貪婪打包，控制在 embedding 友善的大小。
"""

from dataclasses import dataclass

from app.ai.parsers.job_schema import JobParsed

# ~400 tokens（以 ~4 字元/token 估算），落在 300-500 token 目標中段。
_MAX_CHUNK_CHARS = 1600


@dataclass(frozen=True)
class ChunkDraft:
    """尚未寫入 DB 的切塊：段落標籤 + 要 embedding 的文字。"""

    section: str
    content: str


def _render(header: str, items: list[str], *, bullet: bool) -> str:
    """把一組 item 連同段落前綴渲染成單塊文字。"""
    if bullet:
        return header + "".join(f"\n- {it}" for it in items)
    return f"{header} " + ", ".join(items)


def _hard_split(header: str, item: str, *, bullet: bool) -> list[str]:
    """單一 item 本身就超過上限時，按字元邊界硬切成多塊（罕見的防呆路徑）。"""
    overhead = len(_render(header, [""], bullet=bullet))
    body = max(1, _MAX_CHUNK_CHARS - overhead)
    pieces = [item[i : i + body] for i in range(0, len(item), body)]
    return [_render(header, [piece], bullet=bullet) for piece in pieces]


def _pack(section: str, header: str, items: list[str], *, bullet: bool) -> list[ChunkDraft]:
    """把 item 貪婪打包成數塊，每塊（含前綴）不超過上限；空 item / 空段落自動略過。"""
    items = [it.strip() for it in items if it and it.strip()]
    if not items:
        return []

    drafts: list[ChunkDraft] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            drafts.append(
                ChunkDraft(section=section, content=_render(header, current, bullet=bullet))
            )
            current.clear()

    for item in items:
        if current and len(_render(header, [*current, item], bullet=bullet)) <= _MAX_CHUNK_CHARS:
            current.append(item)
            continue
        flush()
        if len(_render(header, [item], bullet=bullet)) <= _MAX_CHUNK_CHARS:
            current.append(item)
        else:
            for piece in _hard_split(header, item, bullet=bullet):
                drafts.append(ChunkDraft(section=section, content=piece))
    flush()
    return drafts


def _overview(parsed: JobParsed) -> list[ChunkDraft]:
    """公司 / 職稱 / 地點 / 工作型態的標頭塊（供 Phase 5 職稱相似度）。全空則略過。"""
    fields = [
        ("Company", parsed.company),
        ("Title", parsed.title),
        ("Location", parsed.location),
        ("Work mode", parsed.work_mode),
    ]
    lines = [f"{label}: {value.strip()}" for label, value in fields if value and value.strip()]
    if not lines:
        return []
    return [ChunkDraft(section="overview", content="\n".join(lines))]


def chunk_job(parsed: JobParsed) -> list[ChunkDraft]:
    """把結構化職缺切成固定順序的 section chunks；空段落略過，全空回 ``[]``。"""
    drafts: list[ChunkDraft] = []
    drafts += _overview(parsed)
    drafts += _pack("responsibilities", "Responsibilities:", parsed.responsibilities, bullet=True)
    drafts += _pack("required_skills", "Required skills:", parsed.required_skills, bullet=False)
    drafts += _pack("preferred_skills", "Preferred skills:", parsed.preferred_skills, bullet=False)
    drafts += _pack("qualifications", "Qualifications:", parsed.qualifications, bullet=True)
    drafts += _pack(
        "experience_requirements",
        "Experience requirements:",
        parsed.experience_requirements,
        bullet=True,
    )
    return drafts
