export interface BasicInfo {
  name: string
  email: string
  phone: string
  location: string
  links: string[]
}

export interface ExperienceItem {
  company: string
  title: string
  start_date: string
  end_date: string
  bullets: string[]
}

export interface ProjectItem {
  name: string
  description: string
  bullets: string[]
  tech: string[]
}

export interface EducationItem {
  school: string
  degree: string
  field: string
  graduation: string
}

export interface ResumeParsed {
  basic_info: BasicInfo
  summary: string
  skills: string[]
  experience: ExperienceItem[]
  projects: ProjectItem[]
  education: EducationItem[]
  certifications: string[]
}

export interface Resume {
  id: string
  source_filename: string | null
  source_type: string
  parse_status: string
  parse_error: string | null
  created_at: string
  current_version_number: number | null
  parsed_data: ResumeParsed | null
}

export interface ResumeVersion {
  id: string
  version_number: number
  label: string
  created_at: string
}

// 全空白結構，給「解析失敗、使用者手動填」或新建空履歷時當編輯器初值。
export function emptyResumeParsed(): ResumeParsed {
  return {
    basic_info: { name: '', email: '', phone: '', location: '', links: [] },
    summary: '',
    skills: [],
    experience: [],
    projects: [],
    education: [],
    certifications: [],
  }
}
