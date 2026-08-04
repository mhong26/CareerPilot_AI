"""GeminiProvider 的單元測試 —— 全程 mock，不連網。

手法：建立 provider 後，用 monkeypatch 把「唯一打網路的 method」
（_generate_content / _embed_content）換成回傳假物件的 stub，藉此精準控制
模型「回了什麼」，測到我們自己的邏輯（解析、驗證 retry、repair、model
fallback、記帳 metadata、正規化）。stub 的第一個參數是 model_name，
依它分流即可模擬「primary 壞、fallback 好」的劇本。
"""

from decimal import Decimal

import pytest
from pydantic import BaseModel

from app.ai.llm.base import LLMError, StructuredOutputError
from app.ai.llm.gemini import GeminiProvider
from app.ai.llm.pricing import estimate_cost


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
    # api_key/model 只需非空；genai.configure 不連網。未設 fallback_model。
    return GeminiProvider(api_key="test-key", model="test-model", embedding_model="test-embed")


@pytest.fixture
def provider_fb():
    """有設定 fallback model 的 provider（fallback 系列測試用）。"""
    return GeminiProvider(
        api_key="test-key",
        model="test-model",
        fallback_model="test-fallback",
        embedding_model="test-embed",
    )


# --- generate -----------------------------------------------------------------


def test_generate_returns_result(provider, monkeypatch):
    monkeypatch.setattr(
        provider,
        "_generate_content",
        lambda model_name, prompt, system, cfg: _FakeResponse("hi there", 3, 4),
    )
    result = provider.generate("say hi")
    assert result.text == "hi there"
    assert result.model == "test-model"
    assert result.usage.prompt_tokens == 3
    assert result.usage.completion_tokens == 4
    assert result.attempts == 1
    assert result.fallback_used is False


def test_generate_wraps_sdk_error(provider, monkeypatch):
    def boom(model_name, prompt, system, cfg):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(provider, "_generate_content", boom)
    with pytest.raises(LLMError):
        provider.generate("say hi")


def test_generate_empty_text_raises(provider, monkeypatch):
    monkeypatch.setattr(
        provider,
        "_generate_content",
        lambda model_name, prompt, system, cfg: _FakeResponse(""),
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
    monkeypatch.setattr(
        provider, "_generate_content", lambda model_name, prompt, system, cfg: next(responses)
    )
    result = provider.generate_structured("extract person", Person)
    assert result.data == Person(name="John", age=30)
    assert result.attempts == 2
    assert result.repair_used is False
    assert result.fallback_used is False


def test_structured_repair_rescues_fenced_json(provider, monkeypatch):
    """兩次都回 markdown-fenced JSON（model_validate_json 失敗）→ repair 救回，repair_used=True。"""
    fenced = _FakeResponse('```json\n{"name": "Amy", "age": 25}\n```')
    monkeypatch.setattr(
        provider, "_generate_content", lambda model_name, prompt, system, cfg: fenced
    )
    result = provider.generate_structured("extract person", Person)
    assert result.data == Person(name="Amy", age=25)
    assert result.repair_used is True
    assert result.fallback_used is False


def test_structured_all_failures_raise(provider, monkeypatch):
    """一直回亂碼 → retry + repair 全敗（未設 fallback）→ StructuredOutputError。"""
    monkeypatch.setattr(
        provider,
        "_generate_content",
        lambda model_name, prompt, system, cfg: _FakeResponse("not json at all"),
    )
    with pytest.raises(StructuredOutputError):
        provider.generate_structured("extract person", Person)


def test_structured_no_fallback_only_hits_primary(provider, monkeypatch):
    """未設 fallback_model → 只打 primary（驗證 retry 兩次），不多打任何模型。"""
    calls: list[str] = []

    def fake(model_name, prompt, system, cfg):
        calls.append(model_name)
        return _FakeResponse("not json at all")

    monkeypatch.setattr(provider, "_generate_content", fake)
    with pytest.raises(StructuredOutputError):
        provider.generate_structured("extract person", Person)
    assert calls == ["test-model", "test-model"]


# --- model fallback（FR-58/59）------------------------------------------------


def test_structured_fallback_rescues_bad_primary(provider_fb, monkeypatch):
    """primary 永遠回壞 JSON → fallback 成功；metadata 與跨嘗試 usage 加總正確。"""

    def fake(model_name, prompt, system, cfg):
        if model_name == "test-model":
            return _FakeResponse("not json at all", 10, 2)
        return _FakeResponse('{"name": "Amy", "age": 25}', 20, 8)

    monkeypatch.setattr(provider_fb, "_generate_content", fake)
    result = provider_fb.generate_structured("extract person", Person)
    assert result.data == Person(name="Amy", age=25)
    assert result.model == "test-fallback"
    assert result.fallback_used is True
    assert result.repair_used is False
    assert result.attempts == 3  # primary 驗證 retry 2 次 + fallback 1 次
    # usage 跨所有嘗試加總（失敗的也有花錢）：10+10+20 / 2+2+8。
    assert result.usage.prompt_tokens == 40
    assert result.usage.completion_tokens == 12


def test_generate_fallback_on_primary_error(provider_fb, monkeypatch):
    """primary 連呼叫都失敗（如網路 retry 耗盡）→ fallback 完整重跑成功。"""

    def fake(model_name, prompt, system, cfg):
        if model_name == "test-model":
            raise RuntimeError("primary exploded")
        return _FakeResponse("rescued", 3, 4)

    monkeypatch.setattr(provider_fb, "_generate_content", fake)
    result = provider_fb.generate("say hi")
    assert result.text == "rescued"
    assert result.model == "test-fallback"
    assert result.fallback_used is True
    assert result.attempts == 2


def test_structured_both_models_fail_carries_metadata(provider_fb, monkeypatch):
    """兩個模型全敗 → StructuredOutputError 例外物件帶記帳 metadata（失敗路徑也要能記帳）。"""
    monkeypatch.setattr(
        provider_fb,
        "_generate_content",
        lambda model_name, prompt, system, cfg: _FakeResponse("garbage", 5, 7),
    )
    with pytest.raises(StructuredOutputError) as excinfo:
        provider_fb.generate_structured("extract person", Person)
    exc = excinfo.value
    assert exc.fallback_used is True
    assert exc.model == "test-fallback"
    assert exc.attempts == 4  # 兩個模型各 2 次驗證 retry
    assert exc.usage.prompt_tokens == 20  # 4 次 × 5，失敗嘗試照樣入帳


def test_structured_cost_sums_per_model_pricing(monkeypatch):
    """成本按各次嘗試「實際用的模型」單價分別計算再加總（flash 2 次 + pro 1 次）。"""
    p = GeminiProvider(
        api_key="test-key", model="gemini-2.5-flash", fallback_model="gemini-2.5-pro"
    )

    def fake(model_name, prompt, system, cfg):
        if model_name == "gemini-2.5-flash":
            return _FakeResponse("not json at all", 100, 10)
        return _FakeResponse('{"name": "A", "age": 1}', 200, 20)

    monkeypatch.setattr(p, "_generate_content", fake)
    result = p.generate_structured("extract person", Person)
    expected = estimate_cost("gemini-2.5-flash", 100, 10) * 2 + estimate_cost(
        "gemini-2.5-pro", 200, 20
    )
    assert result.cost_estimate == expected
    assert expected > Decimal("0")  # 兩個模型都有價目，加總必為正


def test_fallback_same_as_primary_is_deduped():
    """fallback 設成與 primary 相同 → 視為未設定（不會同模型重跑兩次）。"""
    p = GeminiProvider(api_key="test-key", model="same-model", fallback_model="same-model")
    assert p.fallback_model == ""
    assert p._model_chain() == ["same-model"]


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
