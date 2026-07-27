import { type FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { createJob, deleteJob, fetchJobs } from '../api/job'
import { type JobListItem } from '../types/job'

// index_status → 徽章顏色（indexed 綠 / failed 紅 / skipped 灰）。
function statusBadgeClass(status: string): string {
  if (status === 'indexed') return 'text-xs text-green-600'
  if (status === 'failed') return 'text-xs text-red-600'
  return 'text-xs text-gray-400'
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [text, setText] = useState('')

  // 載入職缺列表（mount 時一次）。
  useEffect(() => {
    fetchJobs()
      .then(setJobs)
      .catch(() => setError('Failed to load your jobs.'))
      .finally(() => setLoading(false))
  }, [])

  async function refresh() {
    setJobs(await fetchJobs())
  }

  async function handleAdd(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setWarning(null)
    setAdding(true)
    try {
      const job = await createJob(text)
      setText('')
      if (job.parse_status === 'failed') {
        setWarning('Automatic parsing failed — the job was saved without structured fields.')
      } else if (job.index_status === 'failed') {
        setWarning(
          'The job was saved but embedding indexing failed; it will be excluded from similarity search.',
        )
      }
      await refresh()
    } catch {
      setError('Failed to add the job. Make sure the text is long enough, then try again.')
    } finally {
      setAdding(false)
    }
  }

  async function handleDelete(id: string) {
    setError(null)
    try {
      await deleteJob(id)
      await refresh()
    } catch {
      setError('Failed to delete the job. Please try again.')
    }
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-4">
          <h1 className="text-xl font-bold text-gray-900">Jobs</h1>
          <Link to="/dashboard" className="text-sm text-blue-600 hover:underline">
            ← Dashboard
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl space-y-6 px-4 py-8">
        {error && (
          <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">{error}</div>
        )}
        {warning && (
          <div className="rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">{warning}</div>
        )}

        {/* 新增職缺 */}
        <section className="space-y-4 rounded-lg bg-white p-6 shadow">
          <h2 className="text-lg font-semibold text-gray-900">Add a job</h2>
          <form onSubmit={handleAdd} className="space-y-3">
            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={6}
              placeholder="Paste a job description here…"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
            />
            <button
              type="submit"
              disabled={adding || text.trim().length === 0}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {adding ? 'Adding & parsing…' : 'Add & parse'}
            </button>
          </form>
        </section>

        {/* 職缺列表 */}
        <section className="space-y-3 rounded-lg bg-white p-6 shadow">
          <h2 className="text-lg font-semibold text-gray-900">Your jobs</h2>
          {loading ? (
            <p className="text-sm text-gray-400">Loading…</p>
          ) : jobs.length === 0 ? (
            <p className="text-sm text-gray-400">No jobs yet. Paste one above to get started.</p>
          ) : (
            <ul className="divide-y divide-gray-100 text-sm">
              {jobs.map((job) => (
                <li key={job.id} className="flex items-center justify-between gap-4 py-2">
                  <Link to={`/jobs/${job.id}`} className="min-w-0 flex-1 hover:underline">
                    <span className="font-medium text-gray-800">
                      {job.title ?? 'Untitled job'}
                    </span>
                    {job.company && <span className="text-gray-500"> · {job.company}</span>}
                  </Link>
                  <span className={statusBadgeClass(job.index_status)}>{job.index_status}</span>
                  <span className="text-gray-400">
                    {new Date(job.created_at).toLocaleDateString()}
                  </span>
                  <button
                    type="button"
                    onClick={() => void handleDelete(job.id)}
                    className="text-sm text-red-500 hover:underline"
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  )
}
