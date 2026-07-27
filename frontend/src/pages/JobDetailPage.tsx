import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { deleteJob, fetchJob } from '../api/job'
import { type Job } from '../types/job'

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

export default function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()

  const [job, setJob] = useState<Job | null>(null)
  const [loading, setLoading] = useState(true)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!jobId) return
    fetchJob(jobId)
      .then(setJob)
      .catch(() => setError('Failed to load the job.'))
      .finally(() => setLoading(false))
  }, [jobId])

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

  const parsed = job?.parsed_data ?? null

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

            {/* 解析結果（唯讀）；解析失敗時退回顯示原文 */}
            {parsed ? (
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
            )}
          </>
        )}
      </main>
    </div>
  )
}
