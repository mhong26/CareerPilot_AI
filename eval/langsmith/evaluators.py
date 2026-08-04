"""LLM-as-judge 定義（ER-6 hallucination、ER-3 rubric）。

Judge 一律走 eval 的快取 provider（``gemini-3.6-flash``，無 fallback）：
- 同一 claims / artifact 只 judge 一次——free tier ~20 RPD 下 75 次冷跑呼叫
  可跨日續跑，已 judge 的項目永不重打。
- Judge 呼叫**不寫 LLMCallLog**：它不是 production 流量，不應汙染
  reliability / 延遲統計。

批次原則：hallucination judge **一份報告一次呼叫**（全部 claims 進同一個
prompt、回逐條 verdict 陣列）——把 100 次呼叫壓成 25 次的關鍵額度決策。
"""

from app.ai.llm.base import LLMError
from pydantic import BaseModel, Field

import eval._bootstrap  # noqa: F401  # isort: split
from eval.harness import CachingProvider

_MAX_EVIDENCE_CHARS = 1600  # 對齊 chunking 的單塊上限


# --- Hallucination judge ------------------------------------------------------

HALLUCINATION_JUDGE_SYSTEM = (
    "You are a strict fact-checking judge. You are given a candidate's skill list and a "
    "series of numbered CLAIMS, each asserting that the candidate is missing a skill a "
    "job demands, together with quoted EVIDENCE from the job posting for that claim.\n"
    "For each claim, judge solely from its quoted evidence and the candidate's skill "
    "list — never use outside knowledge about the job, the company, or typical roles:\n"
    "- 'supported': the evidence demands the skill AND the skill list does not contain "
    "it (nor an obvious equivalent).\n"
    "- 'partially_supported': the evidence relates to the claim but does not fully back "
    "it (e.g. the skill is only implied, or the resume shows a close equivalent).\n"
    "- 'unsupported': the evidence does not demand the skill, or the candidate's skill "
    "list clearly contains it.\n"
    "Return one verdict per claim, using each claim's number as claim_index."
)


class ClaimVerdict(BaseModel):
    claim_index: int = Field(default=0, description="The claim number being judged.")
    verdict: str = Field(
        default="",
        description="'supported', 'partially_supported', or 'unsupported'.",
    )
    note: str = Field(default="", description="One short sentence justifying the verdict.")


class HallucinationJudgment(BaseModel):
    verdicts: list[ClaimVerdict] = Field(default_factory=list)


VALID_VERDICTS = {"supported", "partially_supported", "unsupported"}


def normalize_verdict(raw: str) -> str | None:
    """寬鬆正規化（空白 / 連字號 / 大小寫）；無法辨識回 None（列為 unjudged）。"""
    cleaned = raw.strip().lower().replace(" ", "_").replace("-", "_")
    return cleaned if cleaned in VALID_VERDICTS else None


def build_hallucination_prompt(resume_skills: list[str], claims: list[dict]) -> str:
    """``claims``：``{skill, severity, reason, evidence_texts: [str]}``。"""
    lines = [
        "Candidate's skill list: " + (", ".join(resume_skills) or "(none listed)"),
        "",
    ]
    for i, claim in enumerate(claims, start=1):
        lines.append(
            f"Claim {i}: the candidate is missing the skill \"{claim['skill']}\" "
            f"(severity: {claim['severity']}). Stated reason: {claim['reason'] or '(none)'}"
        )
        evidence = claim["evidence_texts"] or ["(no evidence provided)"]
        for text in evidence:
            lines.append(f"Evidence for claim {i}: {text[:_MAX_EVIDENCE_CHARS]}")
        lines.append("")
    lines.append("Judge every claim above and return one verdict per claim_index.")
    return "\n".join(lines)


def judge_hallucination(
    judge: CachingProvider, *, resume_skills: list[str], claims: list[dict]
) -> HallucinationJudgment:
    """單一 judge 呼叫評整份報告的 claims；LLMError 由呼叫端接（額度處理）。"""
    prompt = build_hallucination_prompt(resume_skills, claims)
    result = judge.generate_structured(
        prompt, HallucinationJudgment, system=HALLUCINATION_JUDGE_SYSTEM
    )
    return result.data


def tally_verdicts(claims: list[dict], judgment: HallucinationJudgment) -> dict[str, int]:
    """把 judge 輸出對齊 claims 計數；缺漏 / 無法辨識的 verdict 計入 unjudged。"""
    by_index: dict[int, str] = {}
    for v in judgment.verdicts:
        verdict = normalize_verdict(v.verdict)
        if verdict is not None and 1 <= v.claim_index <= len(claims):
            by_index.setdefault(v.claim_index, verdict)
    counts = {"supported": 0, "partially_supported": 0, "unsupported": 0, "unjudged": 0}
    for i in range(1, len(claims) + 1):
        counts[by_index.get(i, "unjudged")] += 1
    return counts


# --- Rubric judge -------------------------------------------------------------

RUBRIC_SYSTEM = (
    "You are an exacting hiring-coach reviewer. Score the given tailored-resume "
    "suggestions for one candidate targeting one job, on four dimensions, each an "
    "integer 1-5. Anchors:\n"
    "- relevance — 1: generic advice that fits any job; 3: mostly about this job "
    "family but with generic filler; 5: every suggestion clearly tied to this "
    "specific posting's demands.\n"
    "- specificity — 1: vague exhortations ('show more impact'); 3: concrete advice "
    "but few rewritten examples; 5: references concrete bullets/skills from this "
    "resume with ready-to-paste rewrites.\n"
    "- actionability — 1: the candidate would not know what to do next; 3: actionable "
    "with some interpretation; 5: every item can be applied as-is immediately.\n"
    "- alignment — 1: ignores or contradicts the known skill gaps; 3: partially "
    "addresses them; 5: systematically addresses the listed gaps and missing skills "
    "without overclaiming skills the candidate lacks.\n"
    "Judge only from the provided materials. Be strict: reserve 5 for genuinely "
    "excellent work."
)


class RubricScore(BaseModel):
    relevance: int = Field(default=0, description="1-5 per the relevance anchors.")
    specificity: int = Field(default=0, description="1-5 per the specificity anchors.")
    actionability: int = Field(default=0, description="1-5 per the actionability anchors.")
    alignment: int = Field(default=0, description="1-5 per the alignment anchors.")
    rationale: str = Field(default="", description="2-3 sentences justifying the scores.")


RUBRIC_DIMENSIONS = ("relevance", "specificity", "actionability", "alignment")


def build_rubric_prompt(artifact: dict, context: dict) -> str:
    import json

    return (
        "Score these tailored-resume suggestions.\n\n"
        f"Target job: {context.get('job_title')} at {context.get('job_company')}\n"
        f"Job required skills: {', '.join(context.get('job_required_skills') or []) or '(unknown)'}\n"
        f"Job preferred skills: {', '.join(context.get('job_preferred_skills') or []) or '(none)'}\n"
        f"Candidate skills: {', '.join(context.get('resume_skills') or []) or '(none)'}\n"
        f"Candidate summary: {context.get('resume_summary') or '(none)'}\n"
        f"Known missing skills: {', '.join(context.get('missing_skills') or []) or '(none)'}\n"
        f"Known skill gaps: {', '.join(context.get('gap_hints') or []) or '(none)'}\n\n"
        "Suggestions to score (JSON):\n"
        f"{json.dumps(artifact, ensure_ascii=False, indent=2)}"
    )


def judge_rubric(judge: CachingProvider, *, artifact: dict, context: dict) -> RubricScore:
    """單一 judge 呼叫評一份 tailored-resume artifact；分數 clamp 到 1-5。"""
    result = judge.generate_structured(
        build_rubric_prompt(artifact, context), RubricScore, system=RUBRIC_SYSTEM
    )
    score = result.data
    for dim in RUBRIC_DIMENSIONS:
        setattr(score, dim, min(5, max(1, getattr(score, dim))))
    return score


class JudgeQuotaExhausted(Exception):
    """judge 呼叫觸頂（429 / quota）；suite 據此標 partial 並停止後續呼叫。"""


class JudgeCallError(Exception):
    """單次 judge 呼叫失敗（非額度：schema 壞、瞬時網路等）；
    該情境本輪不計分，其餘照評——不能把一次壞回應當額度耗盡而放棄整輪。"""


_QUOTA_MARKERS = ("429", "quota", "resource_exhausted", "rate limit")


def call_judge(fn, *args, **kwargs):
    """judge 呼叫包裝：額度類 LLMError 轉 JudgeQuotaExhausted（停止後續），
    其餘轉 JudgeCallError（跳過單一情境）。"""
    try:
        return fn(*args, **kwargs)
    except LLMError as exc:
        msg = str(exc)
        if any(marker in msg.lower() for marker in _QUOTA_MARKERS):
            raise JudgeQuotaExhausted(msg) from exc
        raise JudgeCallError(msg) from exc
