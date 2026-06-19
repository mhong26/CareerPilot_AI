"""Gemini 實作的 LLMProvider。

第 3 步只完成 ``generate``（純文字生成）+ retry + 用量/延遲統計。
``generate_structured``（第 5 步）與 ``embed``（第 6 步）暫為 stub。

設計上保持「純」：本類別只負責呼叫 Gemini 並回傳 ``LLMResult``，不碰資料庫。
記帳（寫入 llm_call_logs）由 ``services/llm_call_log_service.py`` 負責，方便單元測試。
"""

import time

import google.generativeai as genai
from google.api_core import exceptions as gexc
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ai.llm.base import (
    EmbeddingResult,
    LLMError,
    LLMProvider,
    LLMResult,
    T,
    TokenUsage,
)
from app.core.config import settings

# 只對「暫時性」錯誤重試（伺服器忙、超時、額度節流）；
# 永久性錯誤（key 錯、model 名錯）重試無益，直接往外丟。
_TRANSIENT_ERRORS = (
    gexc.ServiceUnavailable,
    gexc.DeadlineExceeded,
    gexc.InternalServerError,
    gexc.ResourceExhausted,
)


class GeminiProvider(LLMProvider):
    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key:
            raise LLMError("缺少 Gemini API key（請設定 GEMINI_API_KEY）")
        if not model:
            raise LLMError("缺少 Gemini model 名稱（請設定 GEMINI_MODEL）")
        genai.configure(api_key=api_key)
        self.model = model

    @retry(
        retry=retry_if_exception_type(_TRANSIENT_ERRORS),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _call_gemini(self, prompt: str, system: str | None, temperature: float):
        """實際打 API；被 tenacity 包住，暫時性錯誤會指數退避重試。"""
        model = genai.GenerativeModel(self.model, system_instruction=system)
        return model.generate_content(
            prompt,
            generation_config={"temperature": temperature},
        )

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
    ) -> LLMResult:
        start = time.perf_counter()
        try:
            response = self._call_gemini(prompt, system, temperature)
        except _TRANSIENT_ERRORS as exc:
            # 重試耗盡仍失敗。
            raise LLMError(f"Gemini 呼叫失敗（重試後仍失敗）：{exc}") from exc
        except Exception as exc:
            # 永久性 / 非預期錯誤（key 錯、model 名錯…）。
            raise LLMError(f"Gemini 呼叫失敗：{exc}") from exc
        latency_ms = int((time.perf_counter() - start) * 1000)

        text = self._extract_text(response)
        usage = self._extract_usage(response)
        return LLMResult(
            text=text,
            model=self.model,
            usage=usage,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _extract_text(response) -> str:
        """取回應文字；被安全機制擋下或無 candidates 時丟可理解錯誤。"""
        try:
            text = response.text
        except Exception as exc:
            feedback = getattr(response, "prompt_feedback", None)
            raise LLMError(
                f"Gemini 沒有回傳可用文字（可能被安全機制阻擋）：{feedback or exc}"
            ) from exc
        if not text:
            raise LLMError("Gemini 回傳空白內容")
        return text

    @staticmethod
    def _extract_usage(response) -> TokenUsage:
        """從 response.usage_metadata 取 token 用量，缺值以 0 容錯。"""
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return TokenUsage()
        return TokenUsage(
            prompt_tokens=getattr(meta, "prompt_token_count", 0) or 0,
            completion_tokens=getattr(meta, "candidates_token_count", 0) or 0,
            total_tokens=getattr(meta, "total_token_count", 0) or 0,
        )

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
    ) -> tuple[T, TokenUsage]:
        raise NotImplementedError("generate_structured 將在 Phase 2 第 5 步實作")

    def embed(self, texts: list[str]) -> EmbeddingResult:
        raise NotImplementedError("embed 將在 Phase 2 第 6 步實作")


def build_gemini_provider() -> GeminiProvider:
    """依環境設定建立 GeminiProvider 實例。"""
    return GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)
