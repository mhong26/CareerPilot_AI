"""履歷結構化解析提示詞（FR-9）。"""

# 一般履歷遠小於此；防呆避免異常長文字爆 token / 成本（DB 仍存完整原文）。
_MAX_PROMPT_CHARS = 50_000

RESUME_PARSE_SYSTEM = (
    "You are a precise resume parser. Extract information from the resume text "
    "into the provided schema. Use ONLY information explicitly present in the text "
    "— never invent or infer. If a field is absent, use an empty string or an empty "
    "array — always include every field, never omit any. "
    "Preserve the original wording of experience bullet points."
)


def build_resume_parse_prompt(raw_text: str) -> str:
    """包住（截斷後的）履歷原文，組成 user prompt。"""
    text = raw_text[:_MAX_PROMPT_CHARS]
    return (
        "Parse the following resume into the structured schema.\n\n"
        "=== RESUME TEXT START ===\n"
        f"{text}\n"
        "=== RESUME TEXT END ==="
    )
