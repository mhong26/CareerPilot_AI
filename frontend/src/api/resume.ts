import axios from 'axios'

import { apiClient } from './client'
import type { Resume, ResumeParsed, ResumeVersion } from '../types/resume'

// multipart 上傳：apiClient 預設 Content-Type 是 application/json，上傳要用 FormData，
// 必須把該請求的 Content-Type 設成 undefined，讓瀏覽器自動補上帶 boundary 的 multipart
// 標頭（axios 慣用解法）。
const MULTIPART = { headers: { 'Content-Type': undefined } }

export async function uploadResumeFile(file: File): Promise<Resume> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await apiClient.post<Resume>('/resumes/upload', form, MULTIPART)
  return data
}

export async function uploadResumeText(text: string): Promise<Resume> {
  const form = new FormData()
  form.append('text_content', text)
  const { data } = await apiClient.post<Resume>('/resumes/upload', form, MULTIPART)
  return data
}

// /current 無履歷時後端回 404 → 吞掉回 null，讓頁面顯示「尚無履歷」。
export async function fetchCurrentResume(): Promise<Resume | null> {
  try {
    const { data } = await apiClient.get<Resume>('/resumes/current')
    return data
  } catch (e) {
    if (axios.isAxiosError(e) && e.response?.status === 404) return null
    throw e
  }
}

export async function updateResume(id: string, parsed: ResumeParsed): Promise<Resume> {
  const { data } = await apiClient.patch<Resume>(`/resumes/${id}`, { parsed_data: parsed })
  return data
}

export async function fetchVersions(id: string): Promise<ResumeVersion[]> {
  const { data } = await apiClient.get<ResumeVersion[]>(`/resumes/${id}/versions`)
  return data
}
