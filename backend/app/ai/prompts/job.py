"""職缺結構化解析提示詞（FR-14）。"""

# 一般職缺遠小於此；防呆避免異常長文字爆 token / 成本（DB 仍存完整原文）。
_MAX_PROMPT_CHARS = 50_000

JOB_PARSE_SYSTEM = (
    "You are a precise job-description parser. Extract information from the job "
    "posting text into the provided schema. Use ONLY information explicitly present "
    "in the text — never invent or infer. If a field is absent, use an empty string "
    "or an empty array — always include every field, never omit any. "
    "Preserve the original wording of responsibility and qualification lines.\n"
    "Skills: extract each skill as its own separate item. If skills are listed "
    "comma-separated or under a category label (e.g. 'Languages: Python, Go'), drop "
    "the label and split into individual skills. A skill belongs in 'required_skills' "
    "unless the text explicitly marks it as preferred / nice-to-have / a plus / bonus, "
    "in which case it goes in 'preferred_skills'.\n"
    "work_mode: only 'remote', 'hybrid', or 'onsite' when clearly stated; otherwise empty."
)


def build_job_parse_prompt(raw_text: str) -> str:
    """包住（截斷後的）職缺原文，組成 user prompt。"""
    text = raw_text[:_MAX_PROMPT_CHARS]
    return (
        "Parse the following job description into the structured schema.\n\n"
        "=== JOB DESCRIPTION START ===\n"
        f"{text}\n"
        "=== JOB DESCRIPTION END ==="
    )
