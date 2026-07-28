"""Match 分數的純函式計分層（FR-20）。

所有確定性數學集中在此：技能正規化比對、覆蓋率、cosine 與 rescale、年資
解析與加權合成。不碰 DB 也不碰 LLM（LLM 語意等價與 explanation 由
``match_service`` 編排；本模組只驗證與套用其結果），因此可以零 fixture
單元測試（``tests/test_match_scoring.py``）。

權重與校準常數放模組常數而非 settings：Phase 7 agent 以 0.5 / 0.8 門檻對
``match_score`` 路由，公式不得隨部署環境漂移；Phase 9 eval（P@K / MRR）是
調整這些常數的回饋迴路。
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date

from app.ai.parsers.resume_schema import ExperienceItem, ResumeParsed

# 加權合成權重；成分缺失時按剩餘權重歸一化（見 compose_match_score）。
WEIGHTS: dict[str, float] = {
    "embedding_similarity": 0.35,
    "required_coverage": 0.35,
    "preferred_coverage": 0.10,
    "experience_alignment": 0.20,
}

# 履歷向量的三種 kind（與 build_resume_embedding_texts 對應）。
KINDS = ("summary", "skills", "experience")

# Gemini embedding 的 cosine 實際分布壓縮在約 [0.35, 0.95]（完全無關的文字也
# 有 ~0.35），線性拉伸到 [0,1] 才有鑑別度。
_SIM_FLOOR = 0.35
_SIM_CEIL = 0.95


# --- 技能比對 -----------------------------------------------------------------


def normalize_skill(raw: str) -> str:
    """技能字串正規化：小寫、保留 ``c++`` / ``c#`` / ``node.js`` / ``ci-cd``。

    只供「完全相等」比對——刻意不做子字串比對（"java" ⊂ "javascript" 是陷阱）。
    頭尾的 ``.`` 會被剝掉（".net" → "net"），但兩側對稱套用所以不影響比對。
    """
    s = raw.lower().strip()
    s = re.sub(r"[^a-z0-9+#.\- ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .")


def resume_skill_pool(parsed: ResumeParsed) -> list[str]:
    """履歷技能池 = skills + 各 project 的 tech（使用者常把技術只寫在專案裡）。

    正規化去重、保留首見原字串供顯示。
    """
    pool: list[str] = []
    seen: set[str] = set()
    for skill in [*parsed.skills, *(t for p in parsed.projects for t in p.tech)]:
        norm = normalize_skill(skill)
        if norm and norm not in seen:
            seen.add(norm)
            pool.append(skill)
    return pool


def match_skills(job_skills: list[str], resume_pool: list[str]) -> tuple[list[str], list[str]]:
    """Exact 層：正規化後完全相等比對，回 ``(matched, missing)``（job 端原字串）。

    job 端重複技能去重（保序）；空字串忽略。
    """
    pool = {n for s in resume_pool if (n := normalize_skill(s))}
    matched: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    for skill in job_skills:
        norm = normalize_skill(skill)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        (matched if norm in pool else missing).append(skill)
    return matched, missing


def apply_equivalences(
    missing: list[str],
    pairs: list[tuple[str, str]],
    resume_pool: list[str],
) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """套用 LLM 語意等價結果並驗證（幻覺防護）。

    只接受 ``job_skill`` 確實在 missing、``resume_skill`` 確實在履歷技能池的
    pair，其餘丟棄。回 ``(新增 matched, 仍 missing, 通過驗證的 pairs)``。
    """
    missing_by_norm = {normalize_skill(m): m for m in missing}
    pool_norms = {n for s in resume_pool if (n := normalize_skill(s))}
    extra_matched: list[str] = []
    kept_pairs: list[tuple[str, str]] = []
    matched_norms: set[str] = set()
    for job_skill, resume_skill in pairs:
        job_norm = normalize_skill(job_skill)
        if (
            job_norm in missing_by_norm
            and job_norm not in matched_norms
            and normalize_skill(resume_skill) in pool_norms
        ):
            matched_norms.add(job_norm)
            extra_matched.append(missing_by_norm[job_norm])
            kept_pairs.append((job_skill, resume_skill))
    still_missing = [m for m in missing if normalize_skill(m) not in matched_norms]
    return extra_matched, still_missing, kept_pairs


def coverage(matched_count: int, total: int) -> float | None:
    """覆蓋率 = 匹配數 / 總數；該類技能為 0 個時回 None（權重歸一化，非假中性分）。"""
    if total <= 0:
        return None
    return matched_count / total


# --- 向量相似度 ---------------------------------------------------------------


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """兩側皆已 L2 正規化，cosine = 內積；``float()`` 防 numpy 型別滲入 JSONB。"""
    return float(sum(x * y for x, y in zip(a, b, strict=True)))


def rescale_similarity(sim: float) -> float:
    """把 [_SIM_FLOOR, _SIM_CEIL] 線性拉伸到 [0,1]，端點外夾住。"""
    scaled = (sim - _SIM_FLOOR) / (_SIM_CEIL - _SIM_FLOOR)
    return min(1.0, max(0.0, scaled))


def embedding_similarity(
    resume_vecs: Mapping[str, Sequence[float]], chunk_vecs: Sequence[Sequence[float]]
) -> float | None:
    """每種 kind 對所有 chunks 取 max cosine（rescale 後），再對 kinds 取 mean。

    max：skills 向量對上 required_skills chunk 就該得分，不被其他 chunk 平均
    稀釋；mean：不讓單一 kind 獨大。任一側無資料回 None（成分缺失）。
    """
    if not resume_vecs or not chunk_vecs:
        return None
    per_kind = [
        rescale_similarity(max(cosine(rv, cv) for cv in chunk_vecs)) for rv in resume_vecs.values()
    ]
    return float(sum(per_kind) / len(per_kind))


# --- 年資 ---------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip


def _parse_date(raw: str, *, now: date) -> date | None:
    """自由格式日期字串 → date；"Present" 等視為 now；解析失敗回 None。

    支援 "Jan 2026" / "January 2026" / "2026" / "05/2026" / "2026-05"；
    只有年份時月份取 1（start / end 一致，偏差對稱）。
    """
    s = raw.strip().lower()
    if not s:
        return None
    if re.search(r"\b(present|current|now|today|ongoing)\b", s):
        return now
    year_match = re.search(r"\b(19|20)\d{2}\b", s)
    if year_match is None:
        return None
    year = int(year_match.group())
    month = 1
    name_match = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", s)
    if name_match is not None:
        month = _MONTHS[name_match.group(1)]
    else:
        num_match = re.search(r"\b(\d{1,2})\s*/\s*(?:19|20)\d{2}\b", s)  # MM/YYYY
        if num_match is None:
            num_match = re.search(r"\b(?:19|20)\d{2}\s*-\s*(\d{1,2})\b", s)  # YYYY-MM
        if num_match is not None and 1 <= int(num_match.group(1)) <= 12:
            month = int(num_match.group(1))
    return date(year, month, 1)


def total_experience_years(experience: list[ExperienceItem], *, now: date) -> float | None:
    """逐段加總工作月數換算年資（``now`` 注入以利測試）。

    空列表回 0.0（真訊號：沒有經歷）；有列表但全部解析失敗回 None（無法判斷）。
    end 空白但 start 可解析視為仍在職（= now）。
    """
    if not experience:
        return 0.0
    total_months = 0
    any_parsed = False
    for item in experience:
        start = _parse_date(item.start_date, now=now)
        if start is None:
            continue
        end = _parse_date(item.end_date, now=now) if item.end_date.strip() else now
        if end is None or end < start:
            continue
        any_parsed = True
        total_months += (end.year - start.year) * 12 + (end.month - start.month)
    if not any_parsed:
        return None
    return total_months / 12


def extract_required_years(lines: list[str]) -> float | None:
    """從 job 的 experience_requirements 抽最低年資需求。

    每行取範圍下界（"3-5 years" → 3、"3+ years" → 3）；跨行取 max（最嚴格那
    條才是門檻）；全部無法解析回 None。
    """
    best: float | None = None
    for line in lines:
        m = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:\+|(?:-|–|to)\s*\d+(?:\.\d+)?)?\s*years?",
            line.lower(),
        )
        if m is None:
            continue
        value = float(m.group(1))
        best = value if best is None else max(best, value)
    return best


def years_score(resume_years: float | None, required_years: float | None) -> float | None:
    """resume_years / required_years，封頂 1；任一側缺或需求 ≤ 0 回 None。"""
    if resume_years is None or required_years is None or required_years <= 0:
        return None
    return min(resume_years / required_years, 1.0)


def experience_alignment(years: float | None, title_similarity: float | None) -> float | None:
    """年資與職稱兩個子分數取可用者的平均；都缺回 None。"""
    parts = [p for p in (years, title_similarity) if p is not None]
    if not parts:
        return None
    return float(sum(parts) / len(parts))


# --- 合成 ---------------------------------------------------------------------


def compose_match_score(
    components: dict[str, float | None],
) -> tuple[float, dict[str, float]]:
    """加權合成：只對可用成分求和，權重按剩餘者歸一化。

    缺成分不硬給中性分（會汙染排序），而是把該權重拿掉、其餘按比例放大。
    回 ``(score, weights_used)``；``weights_used`` 為歸一化後實際使用的權重
    （存入 breakdown 供稽核）。全部成分缺失回 ``(0.0, {})``。
    """
    available = {k: v for k, v in components.items() if k in WEIGHTS and v is not None}
    total_weight = sum(WEIGHTS[k] for k in available)
    if not available or total_weight <= 0:
        return 0.0, {}
    weights_used = {k: WEIGHTS[k] / total_weight for k in available}
    score = sum(value * weights_used[key] for key, value in available.items())
    return float(min(1.0, max(0.0, score))), weights_used
