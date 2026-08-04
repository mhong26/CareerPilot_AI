"""Eval 執行環境：DB engine / session、providers、計時器。

所有真實服務呼叫都經由這裡建構的資源：eval 專用 DB engine（獨立
``careerpilot_eval``）、包了磁碟快取的 gen / judge provider、以及
wall-clock 計時器（ER-9 / NFR-1 的 p50 / p95 來源）。
"""

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.ai.llm.base import (
    EmbeddingResult,
    LLMError,
    LLMProvider,
    LLMResult,
    StructuredResult,
)
from app.core.config import settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

import eval._bootstrap  # noqa: F401  # isort: split
from eval.cache import CachingProvider, DiskCache
from eval.config import EvalConfig


class EvalEnvironmentError(Exception):
    """eval 執行環境缺件（DB 連不上等），訊息須指出修復動作。"""


# --- DB -----------------------------------------------------------------------


def ensure_database(cfg: EvalConfig) -> Engine:
    """確保 ``careerpilot_eval`` 存在（含 pgvector extension 與全部資料表）。

    走 conftest 的 ``Base.metadata.create_all`` 模式而非 Alembic：eval DB 是
    可隨時重建的衍生品，不需要遷移歷史。
    """
    url = make_url(cfg.eval_db_url)
    maintenance = url.set(database="postgres")
    try:
        admin_engine = create_engine(maintenance, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
        admin_engine.dispose()
    except Exception as exc:
        raise EvalEnvironmentError(
            f"cannot reach Postgres at {maintenance.render_as_string(hide_password=True)} — "
            f"start it with `docker compose up -d db` ({exc})"
        ) from exc

    engine = create_engine(cfg.eval_db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    from app.db import models  # noqa: F401  (把所有 table 註冊進 Base.metadata)
    from app.db.session import Base

    Base.metadata.create_all(bind=engine)
    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


# --- Providers ----------------------------------------------------------------


class OfflineProvider(LLMProvider):
    """無 GEMINI_API_KEY 時的替身：任何呼叫都丟 LLMError。

    搭配 CachingProvider 使用時，**全命中快取的 warm run 完全離線可跑**
    （ER-8：無 key 時本地 metrics 照算）；真的打到內層才會失敗。
    """

    model = settings.gemini_model
    fallback_model = ""
    embedding_model = settings.embedding_model
    embedding_dim = settings.embedding_dim

    def _unavailable(self) -> LLMError:
        return LLMError("GEMINI_API_KEY not set — live LLM calls unavailable in this run")

    def generate(self, prompt, *, system=None, temperature=0.7) -> LLMResult:
        raise self._unavailable()

    def generate_structured(self, prompt, schema, *, system=None) -> StructuredResult:
        raise self._unavailable()

    def embed(self, texts, *, task_type="RETRIEVAL_DOCUMENT") -> EmbeddingResult:
        raise self._unavailable()


@dataclass
class Providers:
    gen: CachingProvider  # production 流程用（primary + fallback chain）
    judge: CachingProvider  # judge 專用（cfg.judge_model，無 fallback）
    offline: bool  # True = 無 API key，只吃得到快取


def build_providers(cfg: EvalConfig) -> Providers:
    cache = DiskCache(cfg.cache_dir / "llm")
    enabled = not cfg.no_cache
    if not settings.gemini_api_key:
        offline = OfflineProvider()
        return Providers(
            gen=CachingProvider(offline, cache, enabled=enabled),
            judge=CachingProvider(offline, cache, enabled=enabled),
            offline=True,
        )

    from app.ai.llm.gemini import GeminiProvider

    gen = GeminiProvider(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        fallback_model=settings.gemini_fallback_model,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
    )
    judge = GeminiProvider(
        api_key=settings.gemini_api_key,
        model=cfg.judge_model,
        fallback_model="",  # judge 不設 fallback：額度是稀缺品，失敗就標 partial
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
    )
    # Client-side 節流（free tier 預設）：避免 429 重試耗盡誤觸 fallback、
    # 把 3.6-flash 額度燒在日常流量；embed 配額（100/min）按批內**每段文字**
    # 各計一次，EVAL_EMBED_MIN_INTERVAL 是 per-text 間隔（0.7s ≈ 85 texts/min，
    # 留重試餘裕）。付費帳號可全部設 0。
    gen_interval = float(os.environ.get("EVAL_GEN_MIN_INTERVAL", "6.5"))
    judge_interval = float(os.environ.get("EVAL_JUDGE_MIN_INTERVAL", "10"))
    embed_interval = float(os.environ.get("EVAL_EMBED_MIN_INTERVAL", "0.7"))
    return Providers(
        gen=CachingProvider(
            gen, cache, enabled=enabled,
            min_interval_s=gen_interval, embed_min_interval_s=embed_interval,
        ),
        judge=CachingProvider(judge, cache, enabled=enabled, min_interval_s=judge_interval),
        offline=False,
    )


# --- 計時 ---------------------------------------------------------------------


class Timings:
    """操作層級 wall-clock 樣本（ER-9 / NFR-1）。

    只有 ``cache_missed=True``（該段操作內發生真實 API 呼叫）的樣本才進
    百分位統計——warm-cache 計時不代表任何真實延遲。樣本持久化到
    ``timings.json``，warm 重跑時報告仍可引用冷跑收集的數字。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.samples: list[dict] = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.samples = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.samples, ensure_ascii=False), encoding="utf-8")

    @contextmanager
    def timed(self, op: str, provider: CachingProvider):
        before = provider.miss_count
        paced_before = provider.paced_seconds
        start = time.perf_counter()
        try:
            yield
        finally:
            # 扣除 client-side 節流的睡眠：那是額度管理，不是系統延遲。
            paced = provider.paced_seconds - paced_before
            self.samples.append(
                {
                    "op": op,
                    "seconds": max(0.0, time.perf_counter() - start - paced),
                    "cache_missed": provider.miss_count > before,
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            )
            self._save()

    def cold_samples(self, op: str) -> list[float]:
        """該操作「含真實 API 呼叫」的秒數樣本。"""
        return [s["seconds"] for s in self.samples if s["op"] == op and s["cache_missed"]]

    def ops(self) -> list[str]:
        return sorted({s["op"] for s in self.samples})
