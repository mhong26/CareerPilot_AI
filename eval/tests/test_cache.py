"""DiskCache / CachingProvider 測試——以計數假 provider 證明快取語意。"""

from decimal import Decimal

import pytest
from app.ai.llm.base import (
    EmbeddingResult,
    LLMProvider,
    LLMResult,
    StructuredResult,
    TokenUsage,
)
from pydantic import BaseModel

import eval._bootstrap  # noqa: F401  # isort: split
from eval.cache import CACHED_MODEL_SUFFIX, CachingProvider, DiskCache


class ToySchema(BaseModel):
    name: str = ""
    score: int = 0


class CountingProvider(LLMProvider):
    """回固定內容並計數呼叫的假 provider。"""

    model = "fake-model"
    fallback_model = ""
    embedding_model = "fake-embedding"
    embedding_dim = 4

    def __init__(self) -> None:
        self.generate_calls = 0
        self.structured_calls = 0
        self.embed_calls = 0
        self.embedded_texts: list[list[str]] = []

    def generate(self, prompt, *, system=None, temperature=0.7):
        self.generate_calls += 1
        return LLMResult(
            text=f"echo:{prompt}",
            model=self.model,
            usage=TokenUsage(10, 5, 15),
            latency_ms=42,
            cost_estimate=Decimal("0.001"),
        )

    def generate_structured(self, prompt, schema, *, system=None):
        self.structured_calls += 1
        return StructuredResult(
            data=schema.model_validate({"name": "cached-me", "score": 7}),
            model=self.model,
            usage=TokenUsage(10, 5, 15),
            latency_ms=42,
            cost_estimate=Decimal("0.001"),
        )

    def embed(self, texts, *, task_type="RETRIEVAL_DOCUMENT"):
        self.embed_calls += 1
        self.embedded_texts.append(list(texts))
        # 向量值編碼文字長度，方便驗證重組順序正確。
        return EmbeddingResult(
            vectors=[[float(len(t)), 0.0, 0.0, 0.0] for t in texts],
            model=self.embedding_model,
            latency_ms=10,
        )


@pytest.fixture
def cached(tmp_path):
    inner = CountingProvider()
    provider = CachingProvider(inner, DiskCache(tmp_path), enabled=True)
    return inner, provider


def test_structured_second_call_hits_cache(cached):
    inner, provider = cached
    first = provider.generate_structured("p1", ToySchema, system="s")
    second = provider.generate_structured("p1", ToySchema, system="s")

    assert inner.structured_calls == 1
    assert provider.miss_count == 1
    assert first.model == "fake-model"
    # hit：model 帶 +cached 後綴、usage / latency 歸零（record_call 汙染防治）
    assert second.model == "fake-model" + CACHED_MODEL_SUFFIX
    assert second.usage.total_tokens == 0
    assert second.latency_ms == 0
    assert second.data == first.data


def test_structured_different_inputs_do_not_collide(cached):
    inner, provider = cached
    provider.generate_structured("p1", ToySchema)
    provider.generate_structured("p2", ToySchema)
    provider.generate_structured("p1", ToySchema, system="different-system")
    assert inner.structured_calls == 3


def test_generate_second_call_hits_cache(cached):
    inner, provider = cached
    provider.generate("hello", temperature=0.3)
    result = provider.generate("hello", temperature=0.3)
    assert inner.generate_calls == 1
    assert result.text == "echo:hello"
    assert result.model.endswith(CACHED_MODEL_SUFFIX)


def test_embed_partial_batch_fetches_only_misses(cached):
    inner, provider = cached
    provider.embed(["aa", "bbbb"])  # 冷跑：一次 API、兩條都入快取
    result = provider.embed(["aa", "cccccc", "bbbb"])  # 只有 cccccc 未命中

    assert inner.embed_calls == 2
    assert inner.embedded_texts[1] == ["cccccc"]  # 殘餘 batch 只含 miss
    # 向量按原順序重組（值 = len(text)）
    assert [v[0] for v in result.vectors] == [2.0, 6.0, 4.0]
    # 有真實 API 呼叫：model 不帶 +cached 後綴
    assert result.model == "fake-embedding"


def test_embed_full_hit_marks_cached_model(cached):
    inner, provider = cached
    provider.embed(["aa"])
    result = provider.embed(["aa"])
    assert inner.embed_calls == 1
    assert result.model == "fake-embedding" + CACHED_MODEL_SUFFIX
    assert result.latency_ms == 0


def test_embed_task_type_separates_cache_entries(cached):
    inner, provider = cached
    provider.embed(["aa"], task_type="RETRIEVAL_DOCUMENT")
    provider.embed(["aa"], task_type="RETRIEVAL_QUERY")
    assert inner.embed_calls == 2


def test_disabled_cache_passes_through(tmp_path):
    inner = CountingProvider()
    provider = CachingProvider(inner, DiskCache(tmp_path), enabled=False)
    provider.generate_structured("p1", ToySchema)
    provider.generate_structured("p1", ToySchema)
    assert inner.structured_calls == 2
    # 停用時也不寫快取檔
    assert not any(tmp_path.rglob("*.json"))


def test_attribute_delegation(cached):
    _, provider = cached
    assert provider.model == "fake-model"
    assert provider.embedding_model == "fake-embedding"
    assert provider.embedding_dim == 4


def test_corrupt_cache_file_treated_as_miss(tmp_path):
    inner = CountingProvider()
    cache = DiskCache(tmp_path)
    provider = CachingProvider(inner, cache, enabled=True)
    provider.generate_structured("p1", ToySchema)
    # 毀掉快取檔 → 下一次視同 miss、重打並修復
    for path in tmp_path.rglob("*.json"):
        path.write_text("{not json", encoding="utf-8")
    provider.generate_structured("p1", ToySchema)
    assert inner.structured_calls == 2
