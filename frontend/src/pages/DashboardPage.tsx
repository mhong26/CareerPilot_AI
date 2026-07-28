import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { fetchMatches, runMatches } from '../api/match'
import { fetchJobs } from '../api/job'
import { fetchCurrentResume } from '../api/resume'
import { useAuth } from '../hooks/useAuth'
import { type JobListItem } from '../types/job'
import { type MatchResultItem } from '../types/match'
import { type Resume } from '../types/resume'

// match_score → 徽章顏色（對齊 Phase 7 routing 門檻：≥0.8 綠 / ≥0.5 黃 / 其餘紅）。
function scoreBadgeClass(score: number): string {
  if (score >= 0.8) return 'text-xs font-semibold text-green-600'
  if (score >= 0.5) return 'text-xs font-semibold text-yellow-600'
  return 'text-xs font-semibold text-red-500'
}

// null = 該成分不可用（後端已把權重歸一化），顯示 n/a 而非 0%。
function formatPercent(value: number | null): string {
  return value !== null ? `${Math.round(value * 100)}%` : 'n/a'
}

// 唯讀清單區塊（同 JobDetailPage 的 ListSection）：空清單不 render。
function TextListSection({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div className="space-y-1">
      <h4 className="text-sm font-semibold uppercase tracking-wide text-gray-500">{title}</h4>
      <ul className="list-inside list-disc space-y-1 text-sm text-gray-800">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

// 成分分數一列。
function BreakdownRow({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex justify-between text-sm">
      <span className="text-gray-500">{label}</span>
      <span className="text-gray-800">{formatPercent(value)}</span>
    </div>
  )
}

// 展開後的單筆詳情：成分分數 + 技能清單 + explanation 四區塊。
function MatchDetails({ match }: { match: MatchResultItem }) {
  const { breakdown, explanation } = match
  return (
    <div className="mt-2 space-y-4 rounded-md bg-gray-50 p-4">
      <div className="grid gap-x-8 gap-y-1 sm:grid-cols-2">
        <BreakdownRow label="Semantic similarity" value={breakdown.embedding_similarity} />
        <BreakdownRow label="Required skills" value={breakdown.required_coverage} />
        <BreakdownRow label="Preferred skills" value={breakdown.preferred_coverage} />
        <BreakdownRow label="Experience fit" value={breakdown.experience_alignment} />
      </div>
      <TextListSection
        title="Matched skills"
        items={[...breakdown.matched_required, ...breakdown.matched_preferred]}
      />
      <TextListSection
        title="Missing skills"
        items={[...breakdown.missing_required, ...breakdown.missing_preferred]}
      />
      {explanation ? (
        <div className="space-y-3 border-t border-gray-200 pt-3">
          {explanation.why_matched && (
            <p className="text-sm text-gray-800">{explanation.why_matched}</p>
          )}
          <TextListSection title="Top overlap" items={explanation.top_overlap} />
          <TextListSection title="Key gaps" items={explanation.missing_skills} />
          <TextListSection title="Risks" items={explanation.risks} />
        </div>
      ) : (
        // explanation 生成失敗：分數仍有效（後端照存），僅解釋缺席。
        <div className="rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
          Explanation generation failed — the score is still valid.
        </div>
      )}
      <p className="text-xs text-gray-400">
        Results reflect the resume at the time of the run (last run{' '}
        {new Date(match.updated_at).toLocaleString()}).
      </p>
    </div>
  )
}

export default function DashboardPage() {
  const { user, logout } = useAuth()

  const [resume, setResume] = useState<Resume | null>(null)
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [matches, setMatches] = useState<MatchResultItem[]>([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  // 載入 current resume + jobs（並行），有履歷再載入既有 match 結果。
  useEffect(() => {
    async function load() {
      try {
        const [resumeData, jobsData] = await Promise.all([fetchCurrentResume(), fetchJobs()])
        setResume(resumeData)
        setJobs(jobsData)
        if (resumeData) {
          setMatches(await fetchMatches(resumeData.id))
        }
      } catch {
        setError('Failed to load your dashboard data.')
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [])

  // 一鍵對全部 job 執行（API 本身支援子集；資料量小，不做勾選 UI）。
  async function handleRun() {
    if (!resume || jobs.length === 0) return
    setError(null)
    setWarning(null)
    setRunning(true)
    try {
      const response = await runMatches(
        resume.id,
        jobs.map((job) => job.id),
      )
      setMatches(response.results)
      if (response.skipped.length > 0) {
        setWarning(`${response.skipped.length} job(s) were skipped (not found or not parsed).`)
      }
    } catch {
      setError('Matching failed. Make sure your resume parsed successfully, then try again.')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow">
        <div className="max-w-5xl mx-auto px-4 py-4 flex items-center justify-between">
          <h1 className="text-xl font-bold text-gray-900">CareerPilot AI</h1>
          <div className="flex items-center gap-4">
            <span className="text-sm text-gray-600">{user?.email}</span>
            <button
              onClick={() => void logout()}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100"
            >
              Log out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto space-y-6 px-4 py-8">
        {error && (
          <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">{error}</div>
        )}
        {warning && (
          <div className="rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">{warning}</div>
        )}

        {/* 歡迎 + 快速入口 */}
        <div className="bg-white rounded-lg shadow p-6 flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              Welcome{user?.full_name ? `, ${user.full_name}` : ''}!
            </h2>
            <p className="text-sm text-gray-500">
              Run matching below to rank your saved jobs against your current resume.
            </p>
          </div>
          <div className="flex shrink-0 gap-3">
            <Link to="/resume" className="text-sm text-blue-600 hover:underline">
              Manage resume
            </Link>
            <Link to="/jobs" className="text-sm text-blue-600 hover:underline">
              Manage jobs
            </Link>
          </div>
        </div>

        {/* 匹配區塊：載入中 / 無履歷 / 無職缺 / ranked list */}
        {loading ? (
          <p className="text-center text-gray-400">Loading…</p>
        ) : !resume ? (
          <section className="space-y-3 rounded-lg bg-white p-6 shadow">
            <h2 className="text-lg font-semibold text-gray-900">Job matches</h2>
            <p className="text-sm text-gray-400">Upload a resume to start matching.</p>
            <Link
              to="/resume"
              className="inline-block rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
            >
              Upload resume
            </Link>
          </section>
        ) : jobs.length === 0 ? (
          <section className="space-y-3 rounded-lg bg-white p-6 shadow">
            <h2 className="text-lg font-semibold text-gray-900">Job matches</h2>
            <p className="text-sm text-gray-400">Add jobs to match against your resume.</p>
            <Link
              to="/jobs"
              className="inline-block rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
            >
              Add jobs
            </Link>
          </section>
        ) : (
          <section className="space-y-3 rounded-lg bg-white p-6 shadow">
            <div className="flex items-center justify-between gap-4">
              <h2 className="text-lg font-semibold text-gray-900">Job matches</h2>
              <button
                type="button"
                onClick={() => void handleRun()}
                disabled={running}
                className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {running ? 'Matching…' : matches.length > 0 ? 'Re-run matching' : 'Run matching'}
              </button>
            </div>
            <p className="text-xs text-gray-400">
              Matching calls the AI once per job and can take a while for many jobs.
            </p>
            {matches.length === 0 ? (
              <p className="text-sm text-gray-400">
                No matches yet. Run matching to rank your jobs.
              </p>
            ) : (
              <ul className="divide-y divide-gray-100 text-sm">
                {matches.map((match, index) => (
                  <li key={match.id} className="py-2">
                    <div className="flex items-center justify-between gap-4">
                      <span className="w-6 shrink-0 text-right text-gray-400">{index + 1}.</span>
                      <Link to={`/jobs/${match.job_id}`} className="min-w-0 flex-1 hover:underline">
                        <span className="font-medium text-gray-800">
                          {match.job_title ?? 'Untitled job'}
                        </span>
                        {match.job_company && (
                          <span className="text-gray-500"> · {match.job_company}</span>
                        )}
                      </Link>
                      <span className={scoreBadgeClass(match.match_score)}>
                        {formatPercent(match.match_score)}
                      </span>
                      <button
                        type="button"
                        aria-expanded={expandedId === match.id}
                        onClick={() =>
                          setExpandedId(expandedId === match.id ? null : match.id)
                        }
                        className="text-sm text-blue-600 hover:underline"
                      >
                        {expandedId === match.id ? 'Hide' : 'Details'}
                      </button>
                    </div>
                    {expandedId === match.id && <MatchDetails match={match} />}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </main>
    </div>
  )
}
