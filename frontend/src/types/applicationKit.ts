// Application kit API 型別，鏡像 backend/app/schemas/application_kit.py 與
// backend/app/ai/parsers/kit_schema.py（snake_case 保留）。

export interface BulletRewrite {
  original: string // 空字串 = 建議新增（非改寫既有句）
  improved: string
  reason: string
}

export interface SectionSuggestion {
  section: string // summary / experience / projects / skills
  bullet_rewrites: BulletRewrite[]
  keywords_to_add: string[]
  note: string
}

export interface TailoredResumeContent {
  overall_strategy: string
  section_suggestions: SectionSuggestion[]
  top_keywords: string[]
}

export interface CoverLetterContent {
  intro: string
  body_paragraphs: string[]
  closing: string
}

export interface InterviewQuestion {
  question: string
  category: string // technical / behavioral / project-based / skill-gap-focused
  why_it_matters: string
  related_resume_area: string
  answer_outline: string[]
}

export interface InterviewPrepContent {
  questions: InterviewQuestion[]
}

// 單一 artifact；content 形狀由 kind 決定（泛型參數對應三種 content）。
export interface KitArtifact<T> {
  id: string
  kind: string // tailored_resume / cover_letter / interview_prep
  source: string // agent / edit
  version_number: number
  run_id: string
  resume_version_number: number
  content: T
  created_at: string
}

// 一組 kit。partial 語意（NFR-4）：缺的 kind 為 null 並列於 missing，
// errors 是 agent run 的降級原因（GET 時恆空）——前端據此顯示重生成引導。
export interface ApplicationKitResponse {
  job_id: string
  resume_id: string
  match_score: number | null
  tailored_resume: KitArtifact<TailoredResumeContent> | null
  cover_letter: KitArtifact<CoverLetterContent> | null
  interview_prep: KitArtifact<InterviewPrepContent> | null
  missing: string[]
  errors: string[]
}
