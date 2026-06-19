"""GeminiProvider 的單元測試 —— 全程 mock，不連網。

手法：建立 provider 後，用 monkeypatch 把「唯一打網路的 method」
（_generate_content / _embed_content）換成回傳假物件的 stub，藉此精準控制
模型「回了什麼」，測到我們自己的邏輯（解析、驗證 retry、fallback、正規化）。
"""

import pytest
from pydantic import BaseModel

from app.ai.llm.base import LLMError, StructuredOutputError
from app.ai.llm.gemini import GeminiProvider


class _FakeUsage:
    def __init__(self, pin: int, pout: int):
        self.prompt_token_count = pin
        self.candidates_token_count = pout
        self.total_token_count = pin + pout


class _FakeResponse:
    """模擬 Gemini SDK 回應：generate 路徑只用到 .text 與 .usage_metadata。"""

    def __init__(self, text: str, pin: int = 5, pout: int = 7):
        self.text = text
        self.usage_metadata = _FakeUsage(pin, pout)


class Person(BaseModel):
    name: str
    age: int


@pytest.fixture
def provider():
    # api_key/model 只需非空；genai.configure 不連網。
    return GeminiProvider(api_key="test-key", model="test-model", embedding_model="test-embed")


# --- generate -----------------------------------------------------------------


def test_generate_returns_result(provider, monkeypatch):
    monkeypatch.setattr(
        provider, "_generate_content", lambda prompt, system, cfg: _FakeResponse("hi there", 3, 4)
    )
    result = provider.generate("say hi")
    assert result.text == "hi there"
    assert result.model == "test-model"
    assert result.usage.prompt_tokens == 3
    assert result.usage.completion_tokens == 4


def test_generate_wraps_sdk_error(provider, monkeypatch):
    def boom(prompt, system, cfg):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(provider, "_generate_content", boom)
    with pytest.raises(LLMError):
        provider.generate("say hi")


def test_generate_empty_text_raises(provider, monkeypatch):
    monkeypatch.setattr(
        provider, "_generate_content", lambda prompt, system, cfg: _FakeResponse("")
    )
    with pytest.raises(LLMError):
        provider.generate("say hi")


# --- generate_structured ------------------------------------------------------


def test_structured_retry_succeeds_on_second_attempt(provider, monkeypatch):
    """核心：第一次回壞 JSON、第二次回好 JSON → 驗證 retry 救回。"""
    responses = iter(
        [
            _FakeResponse('{"name": "John"}'),  # 缺 age，驗證失敗
            _FakeResponse('{"name": "John", "age": 30}'),  # 第二次正確
        ]
    )
    monkeypatch.setattr(provider, "_generate_content", lambda prompt, system, cfg: next(responses))
    obj, usage = provider.generate_structured("extract person", Person)
    assert obj == Person(name="John", age=30)


def test_structured_fallback_repairs_fenced_json(provider, monkeypatch):
    """兩次都回 markdown-fenced JSON（model_validate_json 失敗）→ fallback repair 救回。"""
    fenced = _FakeResponse('```json\n{"name": "Amy", "age": 25}\n```')
    monkeypatch.setattr(provider, "_generate_content", lambda prompt, system, cfg: fenced)
    obj, _ = provider.generate_structured("extract person", Person)
    assert obj == Person(name="Amy", age=25)


def test_structured_all_failures_raise(provider, monkeypatch):
    """一直回亂碼 → retry + fallback 全敗 → StructuredOutputError。"""
    monkeypatch.setattr(
        provider, "_generate_content", lambda prompt, system, cfg: _FakeResponse("not json at all")
    )
    with pytest.raises(StructuredOutputError):
        provider.generate_structured("extract person", Person)


# --- embed --------------------------------------------------------------------


def test_embed_normalizes_vectors(provider, monkeypatch):
    # [3,4] 長度=5 → 正規化成 [0.6, 0.8]；零向量原樣保留。
    monkeypatch.setattr(
        provider, "_embed_content", lambda texts, task_type: {"embedding": [[3.0, 4.0], [0.0, 0.0]]}
    )
    result = provider.embed(["a", "b"])
    assert result.vectors[0] == pytest.approx([0.6, 0.8])
    assert result.vectors[1] == [0.0, 0.0]
    assert result.model == "test-embed"
