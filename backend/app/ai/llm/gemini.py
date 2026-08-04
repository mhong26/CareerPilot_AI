"""Gemini 實作的 LLMProvider。

防護層次（由內到外，Phase 2 建立前三層、Phase 2R 加上第四層）：

1. 網路 retry（tenacity）：暫時性錯誤指數退避重打，同一模型。
2. 驗證 retry：結構化輸出不符 schema 時重新請模型生成，同一模型。
3. repair_json：不打 API，本地修補壞 JSON。
4. **Model fallback（FR-58）**：primary 完整失敗（上面三層全失守，含安全阻擋、
   空回應、model 名錯誤等任何失敗）→ 以 fallback model 把整套流程重跑一次。
   ``embed`` 無 fallback —— 不同 embedding 模型的向量空間互不相容，寧可失敗。

記帳（FR-59）：token / 成本**跨所有嘗試加總**，成本按各次嘗試實際模型的單價
分別計算（失敗的嘗試也有花錢）。全部失敗時 metadata 掛在例外物件上帶出。

設計上保持「純」：本類別只負責呼叫 Gemini 並回傳結果（含 metadata），不碰
資料庫。寫入 llm_call_logs 由 ``services/llm_call_log_service.py`` 負責。
"""

import math
import time
from decimal import Decimal

import google.generativeai as genai
from google.api_core import exceptions as gexc
from langsmith import traceable
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
    StructuredResult,
    T,
    TokenUsage,
)
from app.ai.llm.json_repair import repair_json
from app.ai.llm.pricing import estimate_cost
from app.ai.llm.schema_utils import to_gemini_schema
from app.core.config import settings

# 只對「暫時性」錯誤重試（伺服器忙、超時、額度節流）；
# 永久性錯誤（key 錯、model 名錯）重試無益，直接往外丟（交給 fallback 層決定）。
_TRANSIENT_ERRORS = (
    gexc.ServiceUnavailable,
    gexc.DeadlineExceeded,
    gexc.InternalServerError,
    gexc.ResourceExhausted,
)

# 「網路 retry」：暫時性錯誤指數退避重試。所有「真正打 API」的 method 共用這份設定。
# reraise=True：耗盡後把原始例外原型別往外拋，外層 fallback 迴圈才接得到。
_network_retry = retry(
    retry=retry_if_exception_type(_TRANSIENT_ERRORS),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)

# 「驗證 retry」次數（每個模型各自享有）：連線成功但回傳內容不符 schema 時，
# 重新請該模型生成的最大次數。與「網路 retry」是不同層次的機制。
_STRUCTURED_ATTEMPTS = 2


def _add_usage(total: TokenUsage, delta: TokenUsage) -> TokenUsage:
    """回傳兩份用量的加總（TokenUsage 為 value object，不就地修改）。"""
    return TokenUsage(
        prompt_tokens=total.prompt_tokens + delta.prompt_tokens,
        completion_tokens=total.completion_tokens + delta.completion_tokens,
        total_tokens=total.total_tokens + delta.total_tokens,
    )


class GeminiProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        fallback_model: str = "",
        embedding_model: str = "",
        embedding_dim: int = 768,
    ) -> None:
        if not api_key:
            raise LLMError("缺少 Gemini API key（請設定 GEMINI_API_KEY）")
        if not model:
            raise LLMError("缺少 Gemini model 名稱（請設定 GEMINI_MODEL）")
        genai.configure(api_key=api_key)
        self.model = model
        # 與 primary 相同視為未設定：同一個模型重跑只是加倍花錢，不會有新結果。
        self.fallback_model = fallback_model if fallback_model != model else ""
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim

    def _model_chain(self) -> list[str]:
        """依序嘗試的模型：primary → fallback（若有設定）。"""
        if self.fallback_model:
            return [self.model, self.fallback_model]
        return [self.model]

    @_network_retry
    def _generate_content(
        self, model_name: str, prompt: str, system: str | None, generation_config: dict
    ):
        """唯一真正打 API 的地方。

        必須獨立成一個 method：``@retry`` 只能罩在這層，才不會把外面的計時、
        JSON 驗證、fallback 迴圈也跟著重跑。``model_name`` 由呼叫端傳入，
        讓同一份邏輯能服務 primary 與 fallback 兩個模型。
        """
        model = genai.GenerativeModel(model_name, system_instruction=system)
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

    # @traceable（FR-60）：LANGSMITH_TRACING=true 時上報輸入/輸出/耗時到 LangSmith，
    # 未設定時為 no-op（例外原樣穿透、開銷微秒級）。只裝在公開方法：裝在
    # _generate_content 會把每次網路 retry 都變成獨立 trace（噪音）。
    @traceable(run_type="llm", name="gemini.generate")
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
    ) -> LLMResult:
        config = {"temperature": temperature}
        start = time.perf_counter()
        attempts = 0
        usage_total = TokenUsage()
        cost_total = Decimal("0")
        last_exc: Exception | None = None

        # Fallback 迴圈：primary 的完整流程（網路 retry + 解析）任何失敗 →
        # 換 fallback model 重跑一次；全部失敗才往外丟。
        for model_name in self._model_chain():
            attempts += 1
            try:
                response = self._generate_content(model_name, prompt, system, config)
                text, usage = self._parse(response)
            except Exception as exc:
                # 失敗的嘗試拿不到可靠的 usage（傳輸失敗 / 安全阻擋不回 token 數），
                # 誠實記 0，換下一個模型。
                last_exc = exc
                continue
            usage_total = _add_usage(usage_total, usage)
            cost_total += estimate_cost(model_name, usage.prompt_tokens, usage.completion_tokens)
            return LLMResult(
                text=text,
                model=model_name,
                usage=usage_total,
                latency_ms=int((time.perf_counter() - start) * 1000),
                attempts=attempts,
                fallback_used=model_name != self.model,
                cost_estimate=cost_total,
            )

        chain = self._model_chain()
        raise LLMError(
            f"Gemini 呼叫失敗（嘗試模型：{'、'.join(chain)}）：{last_exc}",
            model=chain[-1],
            attempts=attempts,
            fallback_used=len(chain) > 1,
            usage=usage_total,
            cost_estimate=cost_total,
        ) from last_exc

    # 下面的 ignore：@traceable 會把「泛型方法」包成 SupportsLangsmithExtra，
    # mypy 因此誤判與抽象簽名不相容；runtime 簽名與行為皆不變。
    @traceable(run_type="llm", name="gemini.generate_structured")
    def generate_structured(  # type: ignore[override]
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
    ) -> StructuredResult[T]:
        config = {
            "response_mime_type": "application/json",
            # 送 Gemini 的是清過的 dict（移除 default 等不支援鍵、$ref 內聯）；
            # 驗證仍用原本帶預設值的 Pydantic schema（容錯，見 schema_utils 說明）。
            "response_schema": to_gemini_schema(schema),
            "temperature": 0.1,  # 結構化抽取要穩定，溫度壓低。
        }
        start = time.perf_counter()
        attempts = 0
        usage_total = TokenUsage()
        cost_total = Decimal("0")
        last_error: Exception | None = None

        def _result(data: T, model_name: str, repair_used: bool) -> StructuredResult[T]:
            return StructuredResult(
                data=data,
                model=model_name,
                usage=usage_total,
                cost_estimate=cost_total,
                latency_ms=int((time.perf_counter() - start) * 1000),
                attempts=attempts,
                repair_used=repair_used,
                fallback_used=model_name != self.model,
            )

        # Fallback 迴圈：每個模型各自享有完整的「驗證 retry + repair」流程；
        # primary 全失守才輪到 fallback，全部模型失敗才往外丟。
        for model_name in self._model_chain():
            last_raw = ""

            # 「驗證 retry」迴圈：內容不符 schema 就重新請該模型生一次。
            for _ in range(_STRUCTURED_ATTEMPTS):
                attempts += 1
                try:
                    response = self._generate_content(model_name, prompt, system, config)
                    last_raw, usage = self._parse(response)
                except Exception as exc:
                    # 連回應都拿不到（網路 retry 耗盡 / 安全阻擋 / 空回應）——
                    # 再驗證也沒意義，直接換下一個模型。
                    last_error = exc
                    last_raw = ""
                    break
                usage_total = _add_usage(usage_total, usage)
                cost_total += estimate_cost(
                    model_name, usage.prompt_tokens, usage.completion_tokens
                )
                try:
                    return _result(schema.model_validate_json(last_raw), model_name, False)
                except ValidationError as exc:
                    last_error = exc

            # Repair：對該模型最後一次回應嘗試修復壞 JSON，再驗證一次。
            if last_raw:
                repaired = repair_json(last_raw)
                if repaired is not None:
                    try:
                        return _result(schema.model_validate_json(repaired), model_name, True)
                    except ValidationError as exc:
                        last_error = exc

        chain = self._model_chain()
        raise StructuredOutputError(
            f"結構化輸出在驗證 retry 與 repair 後仍不符 schema"
            f"（嘗試模型：{'、'.join(chain)}）：{last_error}",
            model=chain[-1],
            attempts=attempts,
            repair_used=False,  # 定義：repair_used = 回傳物件經修復而來；失敗即 False。
            fallback_used=len(chain) > 1,
            usage=usage_total,
            cost_estimate=cost_total,
        ) from last_error

    @_network_retry
    def _embed_content(self, texts: list[str], task_type: str):
        """唯一真正打 embedding API 的地方（同 `_generate_content` 的理由：@retry 只罩這層）。"""
        return genai.embed_content(
            model=f"models/{self.embedding_model}",
            content=texts,
            task_type=task_type,
            output_dimensionality=self.embedding_dim,
        )

    @traceable(run_type="embedding", name="gemini.embed")
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
        fallback_model=settings.gemini_fallback_model,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
    )
