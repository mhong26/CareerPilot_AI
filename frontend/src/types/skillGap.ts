// Skill gap API 型別，鏡像 backend/app/schemas/skill_gap.py（snake_case 保留）。

export interface SkillGapItem {
  skill: string
  severity: string // high / medium / low（後端已正規化）
  reason: string
  evidence_chunk_ids: string[]
  suggestion: string
}

// LLM 生成並通過 citation 驗證的分析；dropped_gap_count = 因引用無效被丟棄的 gap 數。
export interface SkillGapPayload {
  gaps: SkillGapItem[]
  overall_summary: string
  dropped_gap_count: number
}

// 檢索到的單塊中繼資料（向量序）；rerank_score 為 null 代表 rerank 未執行或失敗。
export interface RetrievedChunkMeta {
  chunk_id: string
  chunk_index: number
  section: string
  cosine_similarity: number
  rerank_score: number | null
}

// rerank 前（chunks）後（ranked_chunk_ids）兩份排序都保存；失敗時 rerank_used=false。
export interface SkillGapRetrieval {
  query_kind: string
  top_k: number
  chunks: RetrievedChunkMeta[]
  ranked_chunk_ids: string[]
  rerank_used: boolean
  rerank_model: string | null
  rerank_error: string | null
}

// 被引用 chunk 的原文（citation 展開用，隨報告一起回傳、免二次請求）。
export interface SkillGapChunk {
  id: string
  section: string
  content: string
}

// analysis 為 null 代表 LLM 生成失敗（generation_error 有原因）；檢索結果仍有效。
export interface SkillGapReport {
  id: string
  resume_id: string
  resume_version_number: number
  job_id: string
  retrieval: SkillGapRetrieval
  analysis: SkillGapPayload | null
  generation_error: string | null
  chunks: SkillGapChunk[]
  created_at: string
  updated_at: string // = 上次執行時間（重跑覆蓋更新）
}
