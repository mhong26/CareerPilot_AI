"""把 Pydantic 模型轉成 Gemini response_schema 可接受的乾淨 dict。

為什麼需要：Gemini 的 response_schema 只吃 OpenAPI 子集，**不接受 default / title /
additionalProperties / $ref / $defs**（直接丟模型會報 "Unknown field for Schema: default"）。
而我們又想讓 Pydantic 欄位帶預設值（容錯：Gemini 省略某欄時驗證仍能補預設、不失敗）。

解法：送 Gemini 的是「清過、$ref 內聯」的 dict；驗證仍用原本帶預設值的 Pydantic 模型。
"""

from copy import deepcopy
from typing import Any

from pydantic import BaseModel

# Gemini schema 不支援的 JSON Schema 關鍵字，遞迴清除。
_STRIP_KEYS = {"default", "title", "additionalProperties", "$defs", "$ref"}


def to_gemini_schema(model: type[BaseModel]) -> dict[str, Any]:
    """回傳適用於 Gemini ``response_schema`` 的 dict（$ref 內聯、預設值等已移除）。"""
    root = model.model_json_schema()
    defs = root.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:  # 把 #/$defs/X 整段內聯進來
                ref_name = node["$ref"].split("/")[-1]
                return resolve(deepcopy(defs[ref_name]))
            out: dict[str, Any] = {}
            for key, value in node.items():
                if key in _STRIP_KEYS:
                    continue
                if key == "properties" and isinstance(value, dict):
                    # value 的 key 是「欄位名稱」（可能正好叫 title/default…），
                    # 不可當成 schema 關鍵字清掉；只遞迴處理各欄位的子 schema。
                    out[key] = {prop: resolve(sub) for prop, sub in value.items()}
                else:
                    out[key] = resolve(value)
            return out
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(root)
