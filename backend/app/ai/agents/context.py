"""KitRunContext — agent run 的資源容器與暫存區（Phase 7 Step 6）。

工具函式只會收到 LLM 給的參數，但實際工作需要 db session、user、provider
等資源——這些**絕不能讓 LLM 控制**（安全：LLM 不該能指定別人的 user_id；
正確性：db session 不是文字）。解法：資源裝進本 dataclass，
``build_kit_tools(ctx)`` 以 closure 捕獲，LLM 看到的參數表只剩業務參數。

同時作為「精簡回傳原則」的另一半：工具的完整產物（整份履歷、整組建議）
寫進 ctx 暫存區，回給 LLM 的 ToolMessage 只放幾百字摘要——planner 每輪
重讀全部對話，塞大 JSON 會燒 token 且稀釋注意力。
"""

import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.llm.base import LLMProvider
from app.ai.parsers.resume_schema import ResumeParsed
from app.ai.rag.rerank import Reranker
from app.ai.rag.retrieval import RetrievedChunk
from app.db.models import Job, MatchResult, Resume, ResumeVersion, User

# GeneratedArtifact.kind 的三個值——工具層與 graph 完成檢查共用（FR-47~48）。
KIT_KINDS = ("tailored_resume", "cover_letter", "interview_prep")


@dataclass
class KitRunContext:
    # --- 資源（服務層建好，run 全程唯讀）---
    db: Session
    user: User
    resume: Resume
    version: ResumeVersion
    job: Job
    provider: LLMProvider
    reranker: Reranker
    run_id: uuid.UUID
    # time.monotonic() 基準的截止秒數；graph 的 planner node 逾時即收尾（Step 7）。
    deadline: float

    # --- 暫存區（工具寫入、後續工具與服務層讀取）---
    resume_parsed: ResumeParsed | None = None
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    match_result: MatchResult | None = None
    # kind → 已生成尚未保存的 payload（kit_schema 物件）。
    payloads: dict[str, BaseModel] = field(default_factory=dict)
    # kind → 已保存的 GeneratedArtifact.id（graph 完成檢查與 response 組裝的事實來源）。
    saved: dict[str, uuid.UUID] = field(default_factory=dict)
    # 工具/生成失敗的原因累積（NFR-4：不 crash、最後隨 response 回報）。
    errors: list[str] = field(default_factory=list)
