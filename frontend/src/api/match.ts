import { LONG_TASK_TIMEOUT_MS, apiClient } from './client'
import type { MatchResultItem, MatchRunResponse } from '../types/match'

// POST /matches/run：同步計算（每個 job 最多兩次 AI 呼叫），job 多時需時較久，
// 呼叫端要顯示 running 狀態。
export async function runMatches(resumeId: string, jobIds: string[]): Promise<MatchRunResponse> {
  const { data } = await apiClient.post<MatchRunResponse>(
    '/matches/run',
    { resume_id: resumeId, job_ids: jobIds },
    { timeout: LONG_TASK_TIMEOUT_MS },
  )
  return data
}

// GET /matches?resume_id=...：已按 match_score desc 排序。
export async function fetchMatches(resumeId: string): Promise<MatchResultItem[]> {
  const { data } = await apiClient.get<MatchResultItem[]>('/matches', {
    params: { resume_id: resumeId },
  })
  return data
}
