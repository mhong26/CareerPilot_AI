"""LLM provider 抽象介面（合約層）。

整個專案所有用到 LLM 的功能（履歷解析、職缺解析、匹配解釋、求職信、面試題…）
都只依賴這裡定義的 ``LLMProvider`` 介面，而不直接 import 任何廠商 SDK。

如此一來「換模型 / 換廠商」只需新增一個實作此介面的 class（例如 ``gemini.py``），
其餘程式一行都不必改 —— 這對應 SRS 的 FR-62 Provider Abstraction。

本模組只定義「介面長相」與「共用資料 / 例外型別」，不連網、不呼叫任何 API。
實際的 Gemini 呼叫、retry、fallback 由後續的實作層負責。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel

# generate_structured 的輸出型別綁定到 Pydantic BaseModel，
# 讓呼叫端 ``user, usage = provider.generate_structured(prompt, UserSchema)``
# 能拿到正確的型別提示（user 會被推導成 UserSchema 而非 BaseModel）。
T = TypeVar("T", bound=BaseModel)


# --- 共用例外 ----------------------------------------------------------------
# 沿用專案「自訂 Exception」慣例（見 services/auth_service.py），讓實作層與
# service 層共用同一套錯誤型別，呼叫端可精準 except。


class LLMError(Exception):
    """所有 LLM 相關錯誤的基底類別。"""


class StructuredOutputError(LLMError):
    """結構化輸出在 retry 與 fallback 修復都失敗後仍無法產生合法物件時丟出。"""


# --- 用量 / 結果資料物件 ------------------------------------------------------
# 這些是純內部 value object，故用 dataclass（API request/response 才用 Pydantic，
# 與 schemas/auth.py 的分工一致）。


@dataclass
class TokenUsage:
    """單次呼叫的 token 用量，格式統一以便寫入未來的 LLMCallLog（FR-65）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResult:
    """純文字生成的回傳結果。"""

    text: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: float = 0.0


@dataclass
class EmbeddingResult:
    """Embedding 的回傳結果。

    ``vectors`` 與輸入文字一一對應：第 i 條向量是第 i 段文字的 embedding，
    因此天然支援 batch 呼叫。
    """

    vectors: list[list[float]]
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: float = 0.0


# --- 抽象介面 ----------------------------------------------------------------


class LLMProvider(ABC):
    """LLM 廠商必須提供的能力合約。

    這是抽象基底類別：本身無法被實例化，任何子類別都必須補完下列三個方法，
    否則一樣無法實例化 —— 這就是「模型可替換」的強制力來源。
    """

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
    ) -> LLMResult:
        """產生純文字回應。

        Args:
            prompt: 使用者提示詞。
            system: 選用的 system instruction，用來設定模型角色 / 規則。
            temperature: 取樣溫度，越高越發散、越低越穩定保守。

        Raises:
            LLMError: 重試耗盡後仍呼叫失敗。
        """
        raise NotImplementedError

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
    ) -> tuple[T, TokenUsage]:
        """依 Pydantic schema 產生「已驗證」的結構化物件。

        實作層負責 retry 與 fallback（JSON 修復）；合約上保證：回傳的物件
        一定通過 ``schema`` 驗證，否則丟出 ``StructuredOutputError``。

        Args:
            prompt: 描述要抽取 / 生成什麼的提示詞。
            schema: 目標輸出的 Pydantic model 類別。
            system: 選用的 system instruction。

        Returns:
            ``(已驗證的 schema 實例, 本次 token 用量)``。

        Raises:
            StructuredOutputError: retry + fallback 後仍無法產生合法物件。
        """
        raise NotImplementedError

    @abstractmethod
    def embed(
        self,
        texts: list[str],
        *,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> EmbeddingResult:
        """將多段文字轉為向量（batch）。

        Args:
            texts: 待轉換的文字清單。
            task_type: 嵌入用途。存入索引的文件用 ``RETRIEVAL_DOCUMENT``，
                檢索時的查詢用 ``RETRIEVAL_QUERY``（Phase 6 RAG 會用到此區分）。

        Returns:
            ``EmbeddingResult``，其中 ``vectors`` 與 ``texts`` 一一對應。

        Raises:
            LLMError: 重試耗盡後仍呼叫失敗。
        """
        raise NotImplementedError
