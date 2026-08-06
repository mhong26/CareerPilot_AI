import axios from 'axios'

import { LONG_TASK_TIMEOUT_MS, apiClient } from './client'
import type { ApplicationKitResponse, KitArtifact } from '../types/applicationKit'

// POST /jobs/{id}/generate-application-kit：同步跑 agent（多次 AI 呼叫），
// 30~90 秒，呼叫端要顯示 generating 狀態。resumeId 省略 = 用 current resume。
export async function generateApplicationKit(
  jobId: string,
  resumeId?: string,
): Promise<ApplicationKitResponse> {
  const { data } = await apiClient.post<ApplicationKitResponse>(
    `/jobs/${jobId}/generate-application-kit`,
    resumeId ? { resume_id: resumeId } : {},
    { timeout: LONG_TASK_TIMEOUT_MS },
  )
  return data
}

// GET /jobs/{id}/application-kit?resume_id=...：各類 artifact 的最新版；
// 尚未生成（404）→ 吞掉回 null（同 fetchSkillGapForJob 模式）。
export async function fetchApplicationKit(
  jobId: string,
  resumeId: string,
): Promise<ApplicationKitResponse | null> {
  try {
    const { data } = await apiClient.get<ApplicationKitResponse>(
      `/jobs/${jobId}/application-kit`,
      { params: { resume_id: resumeId } },
    )
    return data
  } catch (e) {
    if (axios.isAxiosError(e) && e.response?.status === 404) return null
    throw e
  }
}

// PATCH /artifacts/{id}：保存使用者編輯版——後端 append-only 出新版本 row
// （版號 +1、source="edit"），回傳的新 artifact 直接取代畫面上的舊版。
export async function updateArtifact<T>(artifactId: string, content: T): Promise<KitArtifact<T>> {
  const { data } = await apiClient.patch<KitArtifact<T>>(`/artifacts/${artifactId}`, { content })
  return data
}
