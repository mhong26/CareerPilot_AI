"""Job chunk 檢索（FR-25）：以履歷向量對單一 job 的 chunks 做 top-k 相似度搜尋。

全專案第一個正式 pgvector 查詢。``job_embeddings`` 上冗餘的 ``job_id`` 正是
為此而設（單一 job 內檢索免 join jobs），HNSW cosine 索引已由 Phase 4 建立。
Phase 7 的 agent tool ``retrieve_job_evidence`` 與 Phase 9 retrieval eval 直接
重用本模組。
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobChunk, JobEmbedding

# 職缺典型 4-8 chunks：k=5 覆蓋多數職缺、對長職缺仍有實際篩選；同時就是
# rerank 候選數（corpus 這麼小不需要兩段不同的 k）。模組常數而非 settings：
# Phase 9 eval（P@K / MRR）是調整它的回饋迴路，不得隨部署環境漂移。
TOP_K = 5


@dataclass(frozen=True)
class RetrievedChunk:
    """單筆檢索結果；``cosine_similarity`` 已轉純 Python float（JSONB 防護）。"""

    chunk_id: uuid.UUID
    chunk_index: int
    section: str
    content: str
    cosine_similarity: float


def retrieve_job_chunks(
    db: Session,
    *,
    job_id: uuid.UUID,
    query_vector: list[float],
    top_k: int = TOP_K,
) -> list[RetrievedChunk]:
    """該 job 的 chunks 按 cosine 距離升序取 top-k。

    兩側向量皆已 L2 正規化，cosine distance = 1 - 內積；相似度取 1 - distance
    回傳。pgvector 回傳 numpy float，必須 ``float()``（Phase 5 已知坑：numpy
    型別無法序列化進 JSONB）。
    """
    dist = JobEmbedding.vector.cosine_distance(query_vector).label("dist")
    rows = db.execute(
        select(JobChunk.id, JobChunk.chunk_index, JobChunk.section, JobChunk.content, dist)
        .select_from(JobEmbedding)
        .join(JobChunk, JobChunk.id == JobEmbedding.chunk_id)
        .where(JobEmbedding.job_id == job_id)
        .order_by(dist)
        .limit(top_k)
    ).all()
    return [
        RetrievedChunk(
            chunk_id=chunk_id,
            chunk_index=chunk_index,
            section=section,
            content=content,
            cosine_similarity=float(1.0 - distance),
        )
        for chunk_id, chunk_index, section, content, distance in rows
    ]
