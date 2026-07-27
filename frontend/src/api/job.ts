import axios from 'axios'

import { apiClient } from './client'
import type { Job, JobListItem } from '../types/job'

export async function createJob(rawText: string): Promise<Job> {
  const { data } = await apiClient.post<Job>('/jobs', { raw_text: rawText })
  return data
}

export async function fetchJobs(): Promise<JobListItem[]> {
  const { data } = await apiClient.get<JobListItem[]>('/jobs')
  return data
}

// 不存在（或非本人）時後端回 404 → 吞掉回 null，讓詳細頁顯示「找不到」。
export async function fetchJob(id: string): Promise<Job | null> {
  try {
    const { data } = await apiClient.get<Job>(`/jobs/${id}`)
    return data
  } catch (e) {
    if (axios.isAxiosError(e) && e.response?.status === 404) return null
    throw e
  }
}

export async function deleteJob(id: string): Promise<void> {
  await apiClient.delete(`/jobs/${id}`)
}
