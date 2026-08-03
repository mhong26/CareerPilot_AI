"""LLM provider 抽象介面（合約層）。

整個專案所有用到 LLM 的功能（履歷解析、職缺解析、匹配解釋、求職信、面試題…）
都只依賴這裡定義的 ``LLMProvider`` 介面，而不直接 import 任何廠商 SDK。

如此一來「換模型 / 換廠商」只需新增一個實作此介面的 class（例如 ``gemini.py``），
其餘程式一行都不必改 —— 這對應 SRS 的 FR-53 Provider Abstraction。

本模組只定義「介面長相」與「共用資料 / 例外型別」，不連網、不呼叫任何 API。
實際的 Gemini 呼叫、retry、model fallback 由實作層負責。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel

# generate_structured 的輸出型別綁定到 Pydantic BaseModel，
# 讓呼叫端 ``result = provider.generate_structured(prompt, UserSchema)``
# 能拿到正確的型別提示（result.data 會被推導成 UserSchema 而非 BaseModel）。
T = TypeVar("T", bound=BaseModel)


# --- 用量 / 結果資料物件 ------------------------------------------------------
# 這些是純內部 value object，故用 dataclass（API request/response 才用 Pydantic，
# 與 schemas/auth.py 的分工一致）。


@dataclass
class TokenUsage:
    """單次呼叫的 token 用量，格式統一以便寫入 LLMCallLog（FR-56）。

    有 model fallback（FR-58）後，一次「呼叫」可能含多次「生成嘗試」；
    此物件記錄的是**跨所有嘗試加總**的用量（失敗的嘗試也有花錢，誠實記帳）。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResult:
    """純文字生成的回傳結果。

    Phase 2R 追加記帳 metadata（FR-58/59）：``model`` 為實際成功的模型
    （fallback 觸發時是 fallback 模型）；``cost_estimate`` 按各次嘗試實際
    模型的單價分別計算後加總。
    """

    text: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: float = 0.0
    attempts: int = 1
    fallback_used: bool = False
    cost_estimate: Decimal = Decimal("0")


@dataclass
class StructuredResult(Generic[T]):
    """結構化生成的回傳結果（取代舊的 ``tuple[T, TokenUsage]``）。

    呼叫端記帳所需的一切都在這裡：實際用到的模型只有 provider 知道
    （fallback 可能觸發），必須由回傳值帶出來，不能再從 provider 屬性猜。
    """

    data: T  # 通過 schema 驗證的物件
    model: str  # 實際成功的模型
    usage: TokenUsage = field(default_factory=TokenUsage)  # 跨所有嘗試加總
    cost_estimate: Decimal = Decimal("0")  # 跨嘗試、按各自模型單價加總
    latency_ms: int = 0
    attempts: int = 1  # 生成請求次數（含 fallback；不含網路層 retry）
    repair_used: bool = False  # 回傳物件是否經 repair_json 修復而來
    fallback_used: bool = False


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


# --- 共用例外 ----------------------------------------------------------------
# 沿用專案「自訂 Exception」慣例（見 services/auth_service.py），讓實作層與
# service 層共用同一套錯誤型別，呼叫端可精準 except。


class LLMError(Exception):
    """所有 LLM 相關錯誤的基底類別。

    失敗路徑也要精確記帳（FR-59）：兩個模型都失敗時，呼叫端仍需把
    ``fallback_used=True``、累計 attempts 與已花費的 tokens 寫入 LLMCallLog
    —— 這些資訊只能掛在例外物件上帶出來。全部 keyword-only 且有預設值，
    既有 ``raise LLMError("訊息")`` 寫法完全不受影響。
    """

    def __init__(
        self,
        message: str,
        *,
        model: str = "",
        attempts: int = 1,
        repair_used: bool = False,
        fallback_used: bool = False,
        usage: TokenUsage | None = None,
        cost_estimate: Decimal | None = None,
    ) -> None:
        super().__init__(message)
        self.model = model
        self.attempts = attempts
        self.repair_used = repair_used
        self.fallback_used = fallback_used
        self.usage = usage if usage is not None else TokenUsage()
        self.cost_estimate = cost_estimate if cost_estimate is not None else Decimal("0")


class StructuredOutputError(LLMError):
    """結構化輸出在 retry、repair 與 model fallback 後仍無法產生合法物件時丟出。"""


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
            LLMError: primary（與 fallback，若有設定）皆失敗後丟出，
                例外物件帶記帳 metadata。
        """
        raise NotImplementedError

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
    ) -> StructuredResult[T]:
        """依 Pydantic schema 產生「已驗證」的結構化物件。

        實作層負責驗證 retry、JSON 修復與 model fallback（FR-58）；合約上保證：
        ``result.data`` 一定通過 ``schema`` 驗證，否則丟出 ``StructuredOutputError``。

        Args:
            prompt: 描述要抽取 / 生成什麼的提示詞。
            schema: 目標輸出的 Pydantic model 類別。
            system: 選用的 system instruction。

        Returns:
            ``StructuredResult``：含已驗證物件與記帳 metadata（實際模型、
            跨嘗試加總的 usage / cost、attempts、repair_used、fallback_used）。

        Raises:
            StructuredOutputError: 所有模型的 retry + repair 後仍無法產生
                合法物件（例外物件帶記帳 metadata，原始錯誤以 ``__cause__`` 保留）。
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

        Embedding 無 model fallback：不同 embedding 模型的向量空間互不相容，
        混用會讓相似度比較失真，寧可失敗（FR-58）。

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
