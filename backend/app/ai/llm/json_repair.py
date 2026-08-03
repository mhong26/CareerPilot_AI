"""壞 JSON 的保守修復 —— structured output 的最後一道防線（FR-51）。

LLM 偶爾會把 JSON 包在 markdown ```json fence 裡，或在前後多寫幾句話。
本模組只做「安全、可預期」的修復：拔掉外殼、抓出最外層的 JSON 主體。
修不出合法 JSON 就回 None —— 絕不猜測或臆造內容（亂猜會產生錯誤資料，比失敗更糟）。
"""

import json


def repair_json(raw: str) -> str | None:
    """嘗試從可能夾雜雜訊的字串中抽出合法 JSON；失敗回 None。

    回傳的是「已確認可被 json.loads 解析」的字串，呼叫端可安心餵給 schema 驗證。
    """
    if not raw:
        return None

    candidate = _strip_code_fence(raw).strip()

    # 先試原樣是否就是合法 JSON。
    if _is_valid_json(candidate):
        return candidate

    # 抓出最外層的 {...} 或 [...]，丟掉前後多餘的文字。
    extracted = _extract_outermost(candidate)
    if extracted is not None and _is_valid_json(extracted):
        return extracted

    return None


def _strip_code_fence(text: str) -> str:
    """拔掉 markdown code fence（```json ... ``` 或 ``` ... ```）。"""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    # 去掉開頭那行 fence（可能是 ```json 或 ```）。
    lines = stripped.splitlines()
    lines = lines[1:]
    # 去掉結尾的 fence 行。
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _extract_outermost(text: str) -> str | None:
    """從第一個 { 或 [ 抓到對應的最後一個 } 或 ]（取最外層）。"""
    start_obj = text.find("{")
    start_arr = text.find("[")
    starts = [s for s in (start_obj, start_arr) if s != -1]
    if not starts:
        return None
    start = min(starts)
    open_char = text[start]
    close_char = "}" if open_char == "{" else "]"
    end = text.rfind(close_char)
    if end == -1 or end < start:
        return None
    return text[start : end + 1]


def _is_valid_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except (json.JSONDecodeError, ValueError):
        return False
