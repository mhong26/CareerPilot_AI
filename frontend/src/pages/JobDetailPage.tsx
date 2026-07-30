import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { deleteJob, fetchJob } from '../api/job'
import { fetchCurrentResume } from '../api/resume'
import { fetchSkillGapForJob, generateSkillGap } from '../api/skillGap'
import { type Job } from '../types/job'
import { type Resume } from '../types/resume'
import { type SkillGapChunk, type SkillGapItem, type SkillGapReport } from '../types/skillGap'

// 唯讀清單區塊：空清單就不 render（職缺不可編輯，只展示解析結果）。
function ListSection({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div className="space-y-2 border-t border-gray-100 pt-4 first:border-t-0 first:pt-0">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-500">{title}</h3>
      <ul className="list-inside list-disc space-y-1 text-sm text-gray-800">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

// severity → 整串 className（同 Dashboard 的 scoreBadgeClass 模式）。
function severityBadgeClass(severity: string): string {
  if (severity === 'high') return 'text-xs font-semibold uppercase text-red-500'
  if (severity === 'low') return 'text-xs font-semibold uppercase text-green-600'
  return 'text-xs font-semibold uppercase text-yellow-600'
}

// chunk section → 顯示標題（與 Parsed sections 卡片的標題一致，使用者看得懂）。
const SECTION_TITLES: Record<string, string> = {
  overview: 'Overview',
  responsibilities: 'Responsibilities',
  required_skills: 'Required skills',
  preferred_skills: 'Preferred skills',
  qualifications: 'Qualifications',
  experience_requirements: 'Experience requirements',
}

function sectionTitle(section: string): string {
  return SECTION_TITLES[section] ?? section
}

// 單條 gap 卡片；citation 為單開 accordion（同 Dashboard 的 expandedId 模式），
// key 由父層以 `${gapIndex}:${chunkId}` 控制。
function GapCard({
  gap,
  gapIndex,
  chunkById,
  expandedCitation,
  onToggleCitation,
}: {
  gap: SkillGapItem
  gapIndex: number
  chunkById: Map<string, SkillGapChunk>
  expandedCitation: string | null
  onToggleCitation: (key: string) => void
}) {
  return (
    <div className="space-y-2 border-t border-gray-100 pt-4 first:border-t-0 first:pt-0">
      <div className="flex items-center gap-3">
        <span className="text-sm font-medium text-gray-900">{gap.skill}</span>
        <span className={severityBadgeClass(gap.severity)}>{gap.severity}</span>
      </div>
      {gap.reason && <p className="text-sm text-gray-600">{gap.reason}</p>}
      {gap.suggestion && (
        <p className="text-sm text-gray-800">
          <span className="font-medium">Suggestion:</span> {gap.suggestion}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {gap.evidence_chunk_ids.map((chunkId) => {
          const chunk = chunkById.get(chunkId)
          if (!chunk) return null
          const key = `${gapIndex}:${chunkId}`
          const expanded = expandedCitation === key
          return (
            <button
              key={chunkId}
              type="button"
              aria-expanded={expanded}
              onClick={() => onToggleCitation(key)}
              className="text-sm text-blue-600 hover:underline"
            >
              {expanded ? 'Hide evidence' : `Evidence: ${sectionTitle(chunk.section)}`}
            </button>
          )
        })}
      </div>
      {gap.evidence_chunk_ids.map((chunkId) => {
        const chunk = chunkById.get(chunkId)
        const key = `${gapIndex}:${chunkId}`
        if (!chunk || expandedCitation !== key) return null
        return (
          <div key={chunkId} className="mt-2 space-y-2 rounded-md bg-gray-50 p-4">
            <h4 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
              {sectionTitle(chunk.section)}
            </h4>
            <pre className="whitespace-pre-wrap text-sm text-gray-800">{chunk.content}</pre>
          </div>
        )
      })}
    </div>
  )
}

export default function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()

  const [job, setJob] = useState<Job | null>(null)
  const [loading, setLoading] = useState(true)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Skill gap tab 狀態；tab 用本地 state 而非 nested route（App.tsx 的 "*"
  // catch-all 會吃掉未註冊子路徑，且全站無 URL 狀態先例）。
  const [tab, setTab] = useState<'details' | 'skill-gap'>('details')
  const [resume, setResume] = useState<Resume | null>(null)
  const [resumeLoading, setResumeLoading] = useState(true)
  const [resumeError, setResumeError] = useState<string | null>(null)
  const [report, setReport] = useState<SkillGapReport | null>(null)
  const [loadingReport, setLoadingReport] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [gapError, setGapError] = useState<string | null>(null)
  const [expandedCitation, setExpandedCitation] = useState<string | null>(null)

  useEffect(() => {
    if (!jobId) return
    fetchJob(jobId)
      .then(setJob)
      .catch(() => setError('Failed to load the job.'))
      .finally(() => setLoading(false))
  }, [jobId])

  // 分析需要 resume_id（後端顯式參數，不做隱式 current resume）→ 先取當前履歷。
  // fetchCurrentResume 只吞 404（= 真的沒履歷）；其餘錯誤要另立狀態，
  // 不能折疊成「請先上傳履歷」的錯誤指引。
  useEffect(() => {
    fetchCurrentResume()
      .then(setResume)
      .catch(() => setResumeError('Failed to load your resume. Reload the page to retry.'))
      .finally(() => setResumeLoading(false))
  }, [])

  useEffect(() => {
    if (resumeLoading) return
    if (!job || !resume) {
      setLoadingReport(false)
      return
    }
    // resume 可能先於 job 到達（上面的分支會把 loadingReport 關掉），所以
    // 真正發請求前要重新打開；ignore flag 讓晚到的回應不覆寫較新狀態。
    let ignore = false
    setLoadingReport(true)
    fetchSkillGapForJob(job.id, resume.id)
      .then((r) => {
        if (!ignore) setReport(r)
      })
      .catch(() => {
        if (!ignore) setGapError('Failed to load the skill gap report.')
      })
      .finally(() => {
        if (!ignore) setLoadingReport(false)
      })
    return () => {
      ignore = true
    }
  }, [job, resume, resumeLoading])

  async function handleDelete() {
    if (!job) return
    setError(null)
    setDeleting(true)
    try {
      await deleteJob(job.id)
      navigate('/jobs')
    } catch {
      setError('Failed to delete the job. Please try again.')
      setDeleting(false)
    }
  }

  async function handleGenerate() {
    if (!job || !resume) return
    setGapError(null)
    setGenerating(true)
    try {
      setReport(await generateSkillGap(resume.id, job.id))
      setExpandedCitation(null)
    } catch (e) {
      // 409（履歷未解析 / 職缺未索引）帶可讀 detail，直接顯示；其餘給通用訊息。
      const detail =
        axios.isAxiosError(e) && e.response?.status === 409
          ? (e.response.data as { detail?: string } | undefined)?.detail
          : undefined
      setGapError(detail ?? 'Failed to analyze skill gaps. Please try again.')
    } finally {
      setGenerating(false)
    }
  }

  const parsed = job?.parsed_data ?? null
  const chunkById = useMemo(
    () => new Map((report?.chunks ?? []).map((chunk) => [chunk.id, chunk])),
    [report],
  )

  function tabClass(active: boolean): string {
    return active
      ? 'border-b-2 border-blue-600 pb-2 text-sm font-semibold text-blue-600'
      : 'pb-2 text-sm text-gray-500 hover:text-gray-700'
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-4">
          <h1 className="text-xl font-bold text-gray-900">Job detail</h1>
          <Link to="/jobs" className="text-sm text-blue-600 hover:underline">
            ← Jobs
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl space-y-6 px-4 py-8">
        {error && (
          <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">{error}</div>
        )}

        {loading ? (
          <p className="text-center text-gray-400">Loading…</p>
        ) : !job ? (
          <p className="text-center text-gray-400">Job not found.</p>
        ) : (
          <>
            {job.parse_status === 'failed' && (
              <div className="rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
                Automatic parsing failed{job.parse_error ? `: ${job.parse_error}` : ''}. Only the
                original text is available below.
              </div>
            )}
            {job.index_status === 'failed' && (
              <div className="rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
                Embedding indexing failed{job.index_error ? `: ${job.index_error}` : ''}. This job
                will be excluded from similarity search.
              </div>
            )}

            {/* 標頭：職稱 / 公司 / 地點 / 型態 / 狀態 */}
            <section className="space-y-2 rounded-lg bg-white p-6 shadow">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-lg font-semibold text-gray-900">
                    {job.title ?? 'Untitled job'}
                  </h2>
                  <p className="text-sm text-gray-500">
                    {[job.company, parsed?.location, parsed?.work_mode]
                      .filter(Boolean)
                      .join(' · ')}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void handleDelete()}
                  disabled={deleting}
                  className="text-sm text-red-500 hover:underline disabled:opacity-50"
                >
                  {deleting ? 'Deleting…' : 'Delete'}
                </button>
              </div>
              <p className="text-xs text-gray-400">
                Added {new Date(job.created_at).toLocaleString()} · Indexed as {job.chunk_count}{' '}
                chunk{job.chunk_count === 1 ? '' : 's'} · {job.index_status}
              </p>
            </section>

            {/* 全站第一個 tab UI：Details（原有內容）/ Skill gap analysis */}
            <div role="tablist" className="flex gap-6 border-b border-gray-200">
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'details'}
                onClick={() => setTab('details')}
                className={tabClass(tab === 'details')}
              >
                Details
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'skill-gap'}
                onClick={() => setTab('skill-gap')}
                className={tabClass(tab === 'skill-gap')}
              >
                Skill gap analysis
              </button>
            </div>

            {tab === 'details' &&
              (parsed ? (
                <section className="space-y-4 rounded-lg bg-white p-6 shadow">
                  <h2 className="text-lg font-semibold text-gray-900">Parsed sections</h2>
                  <ListSection title="Responsibilities" items={parsed.responsibilities} />
                  <ListSection title="Required skills" items={parsed.required_skills} />
                  <ListSection title="Preferred skills" items={parsed.preferred_skills} />
                  <ListSection title="Qualifications" items={parsed.qualifications} />
                  <ListSection
                    title="Experience requirements"
                    items={parsed.experience_requirements}
                  />
                </section>
              ) : (
                <section className="space-y-3 rounded-lg bg-white p-6 shadow">
                  <h2 className="text-lg font-semibold text-gray-900">Original text</h2>
                  <pre className="whitespace-pre-wrap text-sm text-gray-800">{job.raw_text}</pre>
                </section>
              ))}

            {tab === 'skill-gap' && (
              <section className="space-y-4 rounded-lg bg-white p-6 shadow">
                <h2 className="text-lg font-semibold text-gray-900">Skill gap analysis</h2>

                {gapError && (
                  <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">
                    {gapError}
                  </div>
                )}

                {resumeLoading ? (
                  <p className="text-center text-gray-400">Loading…</p>
                ) : resumeError ? (
                  <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">
                    {resumeError}
                  </div>
                ) : !resume ? (
                  <p className="text-sm text-gray-600">
                    Upload a resume first to analyze skill gaps.{' '}
                    <Link to="/resume" className="text-blue-600 hover:underline">
                      Go to resume page
                    </Link>
                  </p>
                ) : job.index_status !== 'indexed' ? (
                  <div className="rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
                    This job is not indexed for retrieval, so evidence-based analysis is
                    unavailable. Re-add the job to rebuild its index.
                  </div>
                ) : (
                  <>
                    <div className="space-y-1">
                      <button
                        type="button"
                        onClick={() => void handleGenerate()}
                        disabled={generating || loadingReport}
                        className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
                      >
                        {generating
                          ? 'Analyzing…'
                          : report
                            ? 'Re-run analysis'
                            : 'Analyze skill gaps'}
                      </button>
                      <p className="text-xs text-gray-400">
                        Analysis retrieves job evidence and calls the AI — it can take a little
                        while.
                      </p>
                    </div>

                    {loadingReport ? (
                      <p className="text-center text-gray-400">Loading…</p>
                    ) : !report ? (
                      <p className="text-sm text-gray-400">
                        No analysis yet. Run one to see the gaps between this job and your resume.
                      </p>
                    ) : (
                      <div className="space-y-4">
                        {report.generation_error !== null ? (
                          <>
                            {/* 生成失敗但檢索有效：黃色提示 + 純證據清單（NFR-4 降級）。 */}
                            <div className="rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
                              Gap generation failed — the retrieved evidence below is still valid.
                            </div>
                            {report.chunks.map((chunk) => (
                              <div key={chunk.id} className="space-y-2 rounded-md bg-gray-50 p-4">
                                <h4 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
                                  {sectionTitle(chunk.section)}
                                </h4>
                                <pre className="whitespace-pre-wrap text-sm text-gray-800">
                                  {chunk.content}
                                </pre>
                              </div>
                            ))}
                          </>
                        ) : report.analysis ? (
                          <>
                            {report.analysis.overall_summary && (
                              <p className="text-sm text-gray-800">
                                {report.analysis.overall_summary}
                              </p>
                            )}
                            {report.analysis.gaps.length === 0 ? (
                              <p className="text-sm text-gray-600">
                                No significant skill gaps found for this job.
                              </p>
                            ) : (
                              <div>
                                {report.analysis.gaps.map((gap, i) => (
                                  <GapCard
                                    key={i}
                                    gap={gap}
                                    gapIndex={i}
                                    chunkById={chunkById}
                                    expandedCitation={expandedCitation}
                                    onToggleCitation={(key) =>
                                      setExpandedCitation(expandedCitation === key ? null : key)
                                    }
                                  />
                                ))}
                              </div>
                            )}
                            {report.analysis.dropped_gap_count > 0 && (
                              <p className="text-xs text-gray-400">
                                {report.analysis.dropped_gap_count} gap
                                {report.analysis.dropped_gap_count === 1 ? ' was' : 's were'}{' '}
                                dropped for lacking valid evidence citations.
                              </p>
                            )}
                          </>
                        ) : null}
                        <p className="text-xs text-gray-400">
                          Analyzed {new Date(report.updated_at).toLocaleString()} · resume v
                          {report.resume_version_number}
                          {report.retrieval.rerank_used ? '' : ' · rerank skipped'}
                        </p>
                      </div>
                    )}
                  </>
                )}
              </section>
            )}
          </>
        )}
      </main>
    </div>
  )
}
