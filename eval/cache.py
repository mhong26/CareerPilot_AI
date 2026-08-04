"""LLM / embedding 磁碟快取——free tier 額度下可重跑評估的命脈。

``CachingProvider`` 是 ``LLMProvider`` 的透明 decorator：包住真實
``GeminiProvider`` 後塞進所有 production service，重跑 eval 時相同輸入
直接回快取結果、一次 API 都不打。設計要點：

- **只快取成功**：錯誤永不入快取（retry / fallback 保持 live）。
- **embed 逐文字快取**：batch 只補打未命中的殘餘（一次 API 呼叫），
  向量按原順序重組——跨情境重複的 section 文字只付費一次。
- **LLMCallLog 汙染防治**：cache hit 回傳的 ``model`` 帶 ``+cached`` 後綴、
  usage / cost 歸零、latency 0。service 照常 ``record_call``，但
  ``pricing.estimate_cost`` 對未知 model 回 0（成本不重複計），且
  reliability / system suite 的 SQL 一律過濾 ``model NOT LIKE '%+cached'``。
- 快取檔以內容 sha256 為 key（``root/<k[:2]>/<k>.json``），atomic 寫入。
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from app.ai.llm.base import (
    EmbeddingResult,
    LLMProvider,
    LLMResult,
    StructuredResult,
    TokenUsage,
)
from pydantic import BaseModel

import eval._bootstrap  # noqa: F401  # isort: split

T = TypeVar("T", bound=BaseModel)

CACHED_MODEL_SUFFIX = "+cached"


def cache_key(payload: dict[str, Any]) -> str:
    """payload（method + model + 輸入全文）的 sha256 十六進位 key。"""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DiskCache:
    """單純的 key → JSON dict 磁碟快取；寫入 atomic（tmp + os.replace）。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError):
            return None  # 壞檔視同 miss（下次成功會覆寫）

    def put(self, key: str, value: dict[str, Any]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(value, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise


class CachingProvider(LLMProvider):
    """真實 provider 的透明快取 decorator（services 經 duck typing 無感使用）。

    ``miss_count`` 累計實際打到內層 provider 的呼叫數；harness 的 ``timed``
    以前後快照判定某段操作是否含真實 API 呼叫（warm-cache 計時不進延遲統計）。

    ``min_interval_s``：**client-side 節流**——連續兩次真實生成呼叫的最小
    間隔。free tier 的 RPM 上限若靠 429 重試硬碰，重試耗盡會觸發 model
    fallback、把稀缺的 3.6-flash 額度燒在日常流量上；主動節流可避免。
    節流睡眠累計於 ``paced_seconds``，harness 計時時扣除（不汙染延遲指標）。
    ``embed_min_interval_s`` 為 **per-text** 間隔（配額按批內文字數計）。
    """

    def __init__(
        self,
        inner: LLMProvider,
        cache: DiskCache,
        *,
        enabled: bool = True,
        min_interval_s: float = 0.0,
        embed_min_interval_s: float = 0.0,
    ) -> None:
        self.inner = inner
        self.cache = cache
        self.enabled = enabled
        self.min_interval_s = min_interval_s
        self.embed_min_interval_s = embed_min_interval_s
        self.miss_count = 0
        self.paced_seconds = 0.0
        self._last_live_call = 0.0
        self._next_embed_allowed = 0.0

    def __getattr__(self, name: str) -> Any:
        # model / embedding_model / fallback_model … 全部委派內層——
        # services 以 getattr(provider, "model", ...) 讀取這些屬性。
        # 防護：__init__ 尚未設 inner 前（如反序列化）避免無限遞迴。
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    def _pace(self) -> None:
        """真實生成呼叫前的節流；只在 cache miss 路徑執行。"""
        if self.min_interval_s <= 0:
            return
        import time

        wait = self._last_live_call + self.min_interval_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
            self.paced_seconds += wait
        self._last_live_call = time.monotonic()

    def _pace_embed(self, n_texts: int) -> None:
        """真實 embed 呼叫前的節流（獨立節奏：embedding RPM 上限與生成不同）。

        Free tier 的 ``embed_content_free_tier_requests`` 配額（100/min）把
        **批次裡的每段文字各計一次**——所以間隔必須乘上批次大小，否則一批
        6 chunk 的 job 以「一次呼叫」的節奏連發，實際速率是名目的 6 倍
        （實測整輪 seeding 撞 429 的根因）。此處把本批的配額成本預先記帳：
        下一次呼叫要等到 ``interval × n_texts`` 之後。
        """
        if self.embed_min_interval_s <= 0:
            return
        import time

        wait = self._next_embed_allowed - time.monotonic()
        if wait > 0:
            time.sleep(wait)
            self.paced_seconds += wait
        self._next_embed_allowed = (
            time.monotonic() + self.embed_min_interval_s * max(1, n_texts)
        )

    # --- generate -------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
    ) -> LLMResult:
        key = cache_key(
            {
                "method": "generate",
                "model": self.inner.model,
                "fallback": getattr(self.inner, "fallback_model", ""),
                "system": system or "",
                "prompt": prompt,
                "temperature": temperature,
            }
        )
        if self.enabled and (hit := self.cache.get(key)) is not None:
            return LLMResult(
                text=hit["text"],
                model=hit["model"] + CACHED_MODEL_SUFFIX,
                usage=TokenUsage(),
                latency_ms=0,
            )
        self._pace()
        self.miss_count += 1
        result = self.inner.generate(prompt, system=system, temperature=temperature)
        if self.enabled:
            self.cache.put(key, {"text": result.text, "model": result.model})
        return result

    # --- generate_structured --------------------------------------------------

    def generate_structured(  # type: ignore[override]  # 同 GeminiProvider 的泛型註記
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
    ) -> StructuredResult[T]:
        key = cache_key(
            {
                "method": "generate_structured",
                "model": self.inner.model,
                "fallback": getattr(self.inner, "fallback_model", ""),
                "schema": schema.__name__,
                "system": system or "",
                "prompt": prompt,
            }
        )
        if self.enabled and (hit := self.cache.get(key)) is not None:
            # 讀回時重新過 schema 驗證：快取檔案損毀 / schema 演進時視同 miss。
            try:
                data = schema.model_validate(hit["data"])
            except Exception:
                data = None
            if data is not None:
                return StructuredResult(
                    data=data,
                    model=hit["model"] + CACHED_MODEL_SUFFIX,
                    usage=TokenUsage(),
                    latency_ms=0,
                )
        self._pace()
        self.miss_count += 1
        result = self.inner.generate_structured(prompt, schema, system=system)
        if self.enabled:
            self.cache.put(key, {"data": result.data.model_dump(), "model": result.model})
        return result

    # --- embed ----------------------------------------------------------------

    def _embed_key(self, text: str, task_type: str) -> str:
        return cache_key(
            {
                "method": "embed",
                "model": getattr(self.inner, "embedding_model", ""),
                "dim": getattr(self.inner, "embedding_dim", 0),
                "task_type": task_type,
                "text": text,
            }
        )

    def embed(
        self,
        texts: list[str],
        *,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> EmbeddingResult:
        model_name = getattr(self.inner, "embedding_model", "")
        if not self.enabled:
            self._pace_embed(len(texts))
            self.miss_count += 1
            return self.inner.embed(texts, task_type=task_type)

        keys = [self._embed_key(t, task_type) for t in texts]
        vectors: list[list[float] | None] = []
        for k in keys:
            hit = self.cache.get(k)
            vectors.append(hit["vector"] if hit is not None else None)

        missing = [i for i, v in enumerate(vectors) if v is None]
        if missing:
            # 只補打未命中的殘餘；向量按原 index 放回。
            self._pace_embed(len(missing))
            self.miss_count += 1
            fresh = self.inner.embed([texts[i] for i in missing], task_type=task_type)
            for i, vector in zip(missing, fresh.vectors, strict=True):
                vectors[i] = vector
                self.cache.put(keys[i], {"vector": vector, "model": fresh.model})
            return EmbeddingResult(
                vectors=[v for v in vectors if v is not None],
                model=fresh.model,  # 有真實 API 呼叫：以真實 model 記帳
                usage=fresh.usage,
                latency_ms=fresh.latency_ms,
            )

        return EmbeddingResult(
            vectors=[v for v in vectors if v is not None],
            model=model_name + CACHED_MODEL_SUFFIX,
            usage=TokenUsage(),
            latency_ms=0,
        )
