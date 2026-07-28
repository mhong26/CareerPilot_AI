// Match API 型別，鏡像 backend/app/schemas/match.py（snake_case 保留）。

// LLM 判定並通過後端驗證的等價技能對（如 Go ≈ Golang）。
export interface EquivalentPair {
  job_skill: string
  resume_skill: string
}

// 分數成分明細；成分為 null 代表該成分不可用（權重已歸一化），不是 0 分。
export interface MatchBreakdown {
  embedding_similarity: number | null
  required_coverage: number | null
  preferred_coverage: number | null
  experience_alignment: number | null
  matched_required: string[]
  missing_required: string[]
  matched_preferred: string[]
  missing_preferred: string[]
  equivalent_pairs: EquivalentPair[]
  llm_equivalence_used: boolean
  resume_years: number | null
  required_years: number | null
  years_score: number | null
  title_similarity: number | null
  weights_used: Record<string, number>
}

// LLM 生成的結構化解釋（FR-22）；生成失敗時整個物件為 null。
export interface MatchExplanation {
  why_matched: string
  top_overlap: string[]
  missing_skills: string[]
  risks: string[]
}

// POST /matches/run 與 GET /matches?resume_id=... 共用的單筆結果。
export interface MatchResultItem {
  id: string
  job_id: string
  job_title: string | null
  job_company: string | null
  match_score: number
  breakdown: MatchBreakdown
  explanation: MatchExplanation | null
  explanation_error: string | null
  created_at: string
  updated_at: string // = 上次執行時間（重跑覆蓋更新）
}

// 未能計分的 job："not_found"（不存在 / 非本人）或 "not_parsed"。
export interface MatchSkipped {
  job_id: string
  reason: string
}

// POST /matches/run 的回應；results 已按 match_score desc 排序。
export interface MatchRunResponse {
  resume_id: string
  resume_version_number: number
  results: MatchResultItem[]
  skipped: MatchSkipped[]
}
