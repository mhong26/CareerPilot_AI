"""Gemini 實作的 LLMProvider。

第 3 步完成 ``generate``（純文字）。第 5 步完成 ``generate_structured``
（結構化輸出 + 驗證 + retry + fallback）。``embed``（第 6 步）暫為 stub。

設計上保持「純」：本類別只負責呼叫 Gemini 並回傳結果，不碰資料庫。
記帳（寫入 llm_call_logs）由 ``services/llm_call_log_service.py`` 負責，方便單元測試。
"""

import math
import time

import google.generativeai as genai
from google.api_core import exceptions as gexc
from pydantic import ValidationError
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
    StructuredOutputError,
    T,
    TokenUsage,
)
from app.ai.llm.json_repair import repair_json
from app.core.config import settings

# 只對「暫時性」錯誤重試（伺服器忙、超時、額度節流）；
# 永久性錯誤（key 錯、model 名錯）重試無益，直接往外丟。
_TRANSIENT_ERRORS = (
    gexc.ServiceUnavailable,
    gexc.DeadlineExceeded,
    gexc.InternalServerError,
    gexc.ResourceExhausted,
)

# 「網路 retry」：暫時性錯誤指數退避重試。所有「真正打 API」的 method 共用這份設定。
_network_retry = retry(
    retry=retry_if_exception_type(_TRANSIENT_ERRORS),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)

# 「驗證 retry」次數：連線成功但回傳內容不符 schema 時，重新請模型生成的最大次數。
# 與上面的「網路 retry」是不同層次的機制。
_STRUCTURED_ATTEMPTS = 2


class GeminiProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        embedding_model: str = "",
        embedding_dim: int = 768,
    ) -> None:
        if not api_key:
            raise LLMError("缺少 Gemini API key（請設定 GEMINI_API_KEY）")
        if not model:
            raise LLMError("缺少 Gemini model 名稱（請設定 GEMINI_MODEL）")
        genai.configure(api_key=api_key)
        self.model = model
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim

    @_network_retry
    def _generate_content(self, prompt: str, system: str | None, generation_config: dict):
        """唯一真正打 API 的地方。

        必須獨立成一個 method：``@retry`` 只能罩在這層，才不會把外面的計時、
        JSON 驗證等邏輯也跟著重跑。``generate`` 與 ``generate_structured`` 都用它，
        差別只在傳入的 generation_config。
        """
        model = genai.GenerativeModel(self.model, system_instruction=system)
        # 下面的 ignore：SDK 型別標註未含 response_schema 等鍵，但 runtime 接受。
        return model.generate_content(prompt, generation_config=generation_config)  # type: ignore[arg-type]

    @staticmethod
    def _parse(response) -> tuple[str, TokenUsage]:
        """從回應取出文字與 token 用量；被安全機制擋下或空白時丟可理解錯誤。"""
        try:
            text = response.text
        except Exception as exc:
            feedback = getattr(response, "prompt_feedback", None)
            raise LLMError(
                f"Gemini 沒有回傳可用文字（可能被安全機制阻擋）：{feedback or exc}"
            ) from exc
        if not text:
            raise LLMError("Gemini 回傳空白內容")

        meta = getattr(response, "usage_metadata", None)
        usage = TokenUsage(
            prompt_tokens=getattr(meta, "prompt_token_count", 0) or 0,
            completion_tokens=getattr(meta, "candidates_token_count", 0) or 0,
            total_tokens=getattr(meta, "total_token_count", 0) or 0,
        )
        return text, usage

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
    ) -> LLMResult:
        start = time.perf_counter()
        try:
            response = self._generate_content(prompt, system, {"temperature": temperature})
        except Exception as exc:
            raise LLMError(f"Gemini 呼叫失敗：{exc}") from exc
        latency_ms = int((time.perf_counter() - start) * 1000)

        text, usage = self._parse(response)
        return LLMResult(text=text, model=self.model, usage=usage, latency_ms=latency_ms)

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
    ) -> tuple[T, TokenUsage]:
        config = {
            "response_mime_type": "application/json",
            "response_schema": schema,
            "temperature": 0.1,  # 結構化抽取要穩定，溫度壓低。
        }
        last_raw = ""
        last_usage = TokenUsage()
        last_error: Exception | None = None

        # 「驗證 retry」迴圈：內容不符 schema 就重新請模型生一次。
        for _ in range(_STRUCTURED_ATTEMPTS):
            try:
                response = self._generate_content(prompt, system, config)
            except Exception as exc:
                raise LLMError(f"Gemini 結構化呼叫失敗：{exc}") from exc
            last_raw, last_usage = self._parse(response)
            try:
                return schema.model_validate_json(last_raw), last_usage
            except ValidationError as exc:
                last_error = exc

        # Fallback：對最後一次回應嘗試修復壞 JSON，再驗證一次。
        repaired = repair_json(last_raw)
        if repaired is not None:
            try:
                return schema.model_validate_json(repaired), last_usage
            except ValidationError as exc:
                last_error = exc

        raise StructuredOutputError(
            f"結構化輸出在 {_STRUCTURED_ATTEMPTS} 次重試與 fallback 後仍不符 schema：{last_error}"
        )

    @_network_retry
    def _embed_content(self, texts: list[str], task_type: str):
        """唯一真正打 embedding API 的地方（同 `_generate_content` 的理由：@retry 只罩這層）。"""
        return genai.embed_content(
            model=f"models/{self.embedding_model}",
            content=texts,
            task_type=task_type,
            output_dimensionality=self.embedding_dim,
        )

    def embed(
        self,
        texts: list[str],
        *,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> EmbeddingResult:
        if not self.embedding_model:
            raise LLMError("缺少 embedding model 名稱（請設定 EMBEDDING_MODEL）")

        start = time.perf_counter()
        try:
            response = self._embed_content(texts, task_type)
        except Exception as exc:
            raise LLMError(f"Gemini embedding 呼叫失敗：{exc}") from exc
        latency_ms = int((time.perf_counter() - start) * 1000)

        # embed_content 回傳 {"embedding": [...]}；content 為清單時，embedding 也是清單的清單。
        # L2 正規化成單位向量：MRL 縮維(<3072)回傳的向量未正規化，cosine/dot 會失準；
        # 零向量原樣保留以免除以零。
        vectors = []
        for vec in response["embedding"]:
            norm = math.sqrt(sum(x * x for x in vec))
            vectors.append([x / norm for x in vec] if norm else vec)

        return EmbeddingResult(
            vectors=vectors,
            model=self.embedding_model,
            usage=TokenUsage(),  # embedding API 不回傳 token 數，誠實記 0。
            latency_ms=latency_ms,
        )


def build_gemini_provider() -> GeminiProvider:
    """依環境設定建立 GeminiProvider 實例。"""
    return GeminiProvider(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
    )
