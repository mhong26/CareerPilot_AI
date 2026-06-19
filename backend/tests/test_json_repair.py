"""repair_json 的純函式測試（不連網、不碰 DB）。"""

import json

from app.ai.llm.json_repair import repair_json


def test_strips_markdown_fence():
    raw = '```json\n{"name": "John", "age": 30}\n```'
    fixed = repair_json(raw)
    assert fixed is not None
    assert json.loads(fixed) == {"name": "John", "age": 30}


def test_extracts_json_from_surrounding_prose():
    raw = '好的，結果如下：{"name": "Amy", "age": 25} 謝謝'
    fixed = repair_json(raw)
    assert fixed is not None
    assert json.loads(fixed) == {"name": "Amy", "age": 25}


def test_already_clean_json_passes_through():
    raw = '{"ok": true}'
    assert repair_json(raw) == '{"ok": true}'


def test_repairs_json_array():
    raw = "```\n[1, 2, 3]\n```"
    fixed = repair_json(raw)
    assert fixed is not None
    assert json.loads(fixed) == [1, 2, 3]


def test_garbage_returns_none():
    assert repair_json("這完全不是 JSON") is None


def test_empty_returns_none():
    assert repair_json("") is None
