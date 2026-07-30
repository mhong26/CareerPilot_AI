import axios from 'axios'

import { apiClient } from './client'
import type { SkillGapReport } from '../types/skillGap'

// POST /jobs/{id}/skill-gap：同步跑檢索 + rerank + AI 生成（一次 AI 呼叫），
// 需時較久，呼叫端要顯示 generating 狀態。
export async function generateSkillGap(resumeId: string, jobId: string): Promise<SkillGapReport> {
  const { data } = await apiClient.post<SkillGapReport>(`/jobs/${jobId}/skill-gap`, {
    resume_id: resumeId,
  })
  return data
}

// GET /jobs/{id}/skill-gap?resume_id=...：該 (resume, job) 的既有報告；
// 尚未分析（404）→ 吞掉回 null（同 fetchJob 模式），前端據此顯示 Analyze 按鈕。
export async function fetchSkillGapForJob(
  jobId: string,
  resumeId: string,
): Promise<SkillGapReport | null> {
  try {
    const { data } = await apiClient.get<SkillGapReport>(`/jobs/${jobId}/skill-gap`, {
      params: { resume_id: resumeId },
    })
    return data
  } catch (e) {
    if (axios.isAxiosError(e) && e.response?.status === 404) return null
    throw e
  }
}
