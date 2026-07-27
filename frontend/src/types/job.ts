export interface JobParsed {
  company: string
  title: string
  location: string
  work_mode: string
  responsibilities: string[]
  required_skills: string[]
  preferred_skills: string[]
  qualifications: string[]
  experience_requirements: string[]
}

// GET /jobs 列表的輕量單筆（不含 parsed_data / raw_text）。
export interface JobListItem {
  id: string
  company: string | null
  title: string | null
  parse_status: string
  index_status: string
  created_at: string
}

// POST /jobs、GET /jobs/{id} 的完整回應。
export interface Job {
  id: string
  company: string | null
  title: string | null
  parse_status: string
  parse_error: string | null
  index_status: string
  index_error: string | null
  created_at: string
  raw_text: string
  chunk_count: number
  parsed_data: JobParsed | null
}
