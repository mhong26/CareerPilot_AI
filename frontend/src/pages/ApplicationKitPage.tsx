import { useEffect, useState } from 'react'
import axios from 'axios'
import { Link, useParams } from 'react-router-dom'

import { fetchApplicationKit, generateApplicationKit, updateArtifact } from '../api/applicationKit'
import { fetchJob } from '../api/job'
import { fetchCurrentResume } from '../api/resume'
import {
  copyText,
  coverLetterToMarkdown,
  downloadMarkdown,
  interviewPrepToMarkdown,
  tailoredResumeToMarkdown,
} from '../lib/export'
import {
  type ApplicationKitResponse,
  type CoverLetterContent,
  type InterviewPrepContent,
  type KitArtifact,
  type TailoredResumeContent,
} from '../types/applicationKit'
import { type Job } from '../types/job'
import { type Resume } from '../types/resume'

// match score → 配色（同 Dashboard 的 scoreBadgeClass；門檻 = agent routing 門檻）。
function scoreBadgeClass(score: number): string {
  if (score >= 0.8) return 'text-sm font-semibold text-green-600'
  if (score >= 0.5) return 'text-sm font-semibold text-yellow-600'
  return 'text-sm font-semibold text-red-500'
}

// artifact kind → 顯示標題。
const KIND_TITLES: Record<string, string> = {
  tailored_resume: 'Resume suggestions',
  cover_letter: 'Cover letter',
  interview_prep: 'Interview prep',
}

// 卡片工具列：版本徽章 + Copy / Download / Edit。Copy 成功後短暫顯示 Copied。
function ArtifactToolbar({
  artifact,
  markdown,
  filename,
  editing,
  onToggleEdit,
}: {
  artifact: KitArtifact<unknown>
  markdown: string
  filename: string
  editing: boolean
  onToggleEdit: () => void
}) {
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    try {
      await copyText(markdown)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // clipboard 不可用（權限 / 非安全來源）時靜默略過——下載仍可用。
    }
  }

  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-gray-400">
        v{artifact.version_number}
        {artifact.source === 'edit' ? ' · edited' : ''}
      </span>
      <button
        type="button"
        onClick={() => void handleCopy()}
        className="text-sm text-blue-600 hover:underline"
      >
        {copied ? 'Copied!' : 'Copy'}
      </button>
      <button
        type="button"
        onClick={() => downloadMarkdown(filename, markdown)}
        className="text-sm text-blue-600 hover:underline"
      >
        Download .md
      </button>
      <button type="button" onClick={onToggleEdit} className="text-sm text-blue-600 hover:underline">
        {editing ? 'Cancel' : 'Edit'}
      </button>
    </div>
  )
}

function SaveRow({ saving, onSave }: { saving: boolean; onSave: () => void }) {
  return (
    <button
      type="button"
      onClick={onSave}
      disabled={saving}
      className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
    >
      {saving ? 'Saving…' : 'Save changes'}
    </button>
  )
}

// --- Resume suggestions 卡片 ---------------------------------------------------

function TailoredResumeCard({
  artifact,
  onSaved,
  onError,
}: {
  artifact: KitArtifact<TailoredResumeContent>
  onSaved: (a: KitArtifact<TailoredResumeContent>) => void
  onError: (message: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [draft, setDraft] = useState<TailoredResumeContent>(artifact.content)

  function startEdit() {
    // 深拷貝：編輯草稿不可污染畫面上的已存版本。
    setDraft(JSON.parse(JSON.stringify(artifact.content)) as TailoredResumeContent)
    setEditing(!editing)
  }

  async function handleSave() {
    setSaving(true)
    try {
      onSaved(await updateArtifact(artifact.id, draft))
      setEditing(false)
    } catch {
      onError('Failed to save resume suggestions. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  const content = artifact.content
  return (
    <section className="space-y-4 rounded-lg bg-white p-6 shadow">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-lg font-semibold text-gray-900">{KIND_TITLES.tailored_resume}</h2>
        <ArtifactToolbar
          artifact={artifact}
          markdown={tailoredResumeToMarkdown(content)}
          filename="resume-suggestions.md"
          editing={editing}
          onToggleEdit={startEdit}
        />
      </div>

      {editing ? (
        <div className="space-y-4">
          {draft.section_suggestions.map((section, si) => (
            <div key={si} className="space-y-2 border-t border-gray-100 pt-4 first:border-t-0 first:pt-0">
              <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
                {section.section || 'general'}
              </h3>
              {section.bullet_rewrites.map((rewrite, ri) => (
                <textarea
                  key={ri}
                  aria-label={`Rewrite ${si + 1}-${ri + 1}`}
                  value={rewrite.improved}
                  onChange={(e) =>
                    // immutable 更新：不可就地改 state 內的巢狀物件。
                    setDraft({
                      ...draft,
                      section_suggestions: draft.section_suggestions.map((s, i) =>
                        i !== si
                          ? s
                          : {
                              ...s,
                              bullet_rewrites: s.bullet_rewrites.map((r, j) =>
                                j !== ri ? r : { ...r, improved: e.target.value },
                              ),
                            },
                      ),
                    })
                  }
                  rows={2}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                />
              ))}
            </div>
          ))}
          <SaveRow saving={saving} onSave={() => void handleSave()} />
        </div>
      ) : (
        <div className="space-y-4">
          {content.overall_strategy && (
            <p className="text-sm text-gray-800">{content.overall_strategy}</p>
          )}
          {content.section_suggestions.map((section, si) => (
            <div key={si} className="space-y-2 border-t border-gray-100 pt-4 first:border-t-0 first:pt-0">
              <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
                {section.section || 'general'}
              </h3>
              {section.bullet_rewrites.map((rewrite, ri) => (
                <div key={ri} className="space-y-1">
                  {rewrite.original && (
                    <p className="text-sm text-gray-400 line-through">{rewrite.original}</p>
                  )}
                  <p className="text-sm text-gray-900">{rewrite.improved}</p>
                  {rewrite.reason && <p className="text-xs text-gray-500">{rewrite.reason}</p>}
                </div>
              ))}
              {section.keywords_to_add.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {section.keywords_to_add.map((keyword) => (
                    <span
                      key={keyword}
                      className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-700"
                    >
                      {keyword}
                    </span>
                  ))}
                </div>
              )}
              {section.note && <p className="text-xs text-gray-500">{section.note}</p>}
            </div>
          ))}
          {content.top_keywords.length > 0 && (
            <p className="text-xs text-gray-500">
              <span className="font-medium">Top keywords:</span> {content.top_keywords.join(', ')}
            </p>
          )}
        </div>
      )}
    </section>
  )
}

// --- Cover letter 卡片 ----------------------------------------------------------

function CoverLetterCard({
  artifact,
  onSaved,
  onError,
}: {
  artifact: KitArtifact<CoverLetterContent>
  onSaved: (a: KitArtifact<CoverLetterContent>) => void
  onError: (message: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [intro, setIntro] = useState('')
  const [body, setBody] = useState('')
  const [closing, setClosing] = useState('')

  function startEdit() {
    setIntro(artifact.content.intro)
    // body 段落以空行相接編輯，存檔時再切回段落陣列（結構化區塊，FR-39）。
    setBody(artifact.content.body_paragraphs.join('\n\n'))
    setClosing(artifact.content.closing)
    setEditing(!editing)
  }

  async function handleSave() {
    setSaving(true)
    try {
      const content: CoverLetterContent = {
        intro,
        body_paragraphs: body
          .split(/\n\s*\n/)
          .map((p) => p.trim())
          .filter(Boolean),
        closing,
      }
      onSaved(await updateArtifact(artifact.id, content))
      setEditing(false)
    } catch {
      onError('Failed to save the cover letter. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  const content = artifact.content
  return (
    <section className="space-y-4 rounded-lg bg-white p-6 shadow">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-lg font-semibold text-gray-900">{KIND_TITLES.cover_letter}</h2>
        <ArtifactToolbar
          artifact={artifact}
          markdown={coverLetterToMarkdown(content)}
          filename="cover-letter.md"
          editing={editing}
          onToggleEdit={startEdit}
        />
      </div>

      {editing ? (
        <div className="space-y-3">
          <label className="block text-sm text-gray-600">
            Intro
            <textarea
              value={intro}
              onChange={(e) => setIntro(e.target.value)}
              rows={3}
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="block text-sm text-gray-600">
            Body (blank line between paragraphs)
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={8}
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="block text-sm text-gray-600">
            Closing
            <textarea
              value={closing}
              onChange={(e) => setClosing(e.target.value)}
              rows={3}
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
            />
          </label>
          <SaveRow saving={saving} onSave={() => void handleSave()} />
        </div>
      ) : (
        <div className="space-y-3 text-sm text-gray-800">
          {[content.intro, ...content.body_paragraphs, content.closing]
            .filter(Boolean)
            .map((paragraph, i) => (
              <p key={i}>{paragraph}</p>
            ))}
        </div>
      )}
    </section>
  )
}

// --- Interview prep 卡片 --------------------------------------------------------

// 題目分類 → 配色徽章。
function categoryBadgeClass(category: string): string {
  if (category === 'technical') return 'rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-700'
  if (category === 'behavioral')
    return 'rounded-full bg-green-50 px-2 py-0.5 text-xs text-green-700'
  if (category === 'skill-gap-focused')
    return 'rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-600'
  return 'rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600'
}

function InterviewPrepCard({
  artifact,
  onSaved,
  onError,
}: {
  artifact: KitArtifact<InterviewPrepContent>
  onSaved: (a: KitArtifact<InterviewPrepContent>) => void
  onError: (message: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  // 編輯狀態存「原始字串」（question / 換行相接的 outline）：split/trim 只在
  // 存檔時做——綁在 onChange 上會邊打字邊吃掉空格與空行。
  const [edits, setEdits] = useState<{ question: string; outline: string }[]>([])
  // 單開 accordion（同 GapCard 的 expandedCitation 模式）。
  const [expanded, setExpanded] = useState<number | null>(null)

  function startEdit() {
    setEdits(
      artifact.content.questions.map((q) => ({
        question: q.question,
        outline: q.answer_outline.join('\n'),
      })),
    )
    setEditing(!editing)
  }

  async function handleSave() {
    setSaving(true)
    try {
      const content: InterviewPrepContent = {
        questions: artifact.content.questions.map((q, i) => ({
          ...q,
          question: edits[i]?.question ?? q.question,
          answer_outline: (edits[i]?.outline ?? q.answer_outline.join('\n'))
            .split('\n')
            .map((line) => line.trim())
            .filter(Boolean),
        })),
      }
      onSaved(await updateArtifact(artifact.id, content))
      setEditing(false)
    } catch {
      onError('Failed to save interview prep. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  const content = artifact.content
  return (
    <section className="space-y-4 rounded-lg bg-white p-6 shadow">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-lg font-semibold text-gray-900">{KIND_TITLES.interview_prep}</h2>
        <ArtifactToolbar
          artifact={artifact}
          markdown={interviewPrepToMarkdown(content)}
          filename="interview-prep.md"
          editing={editing}
          onToggleEdit={startEdit}
        />
      </div>

      {editing ? (
        <div className="space-y-4">
          {edits.map((edit, qi) => (
            <div key={qi} className="space-y-2 border-t border-gray-100 pt-4 first:border-t-0 first:pt-0">
              <textarea
                aria-label={`Question ${qi + 1}`}
                value={edit.question}
                onChange={(e) =>
                  setEdits(
                    edits.map((item, i) =>
                      i !== qi ? item : { ...item, question: e.target.value },
                    ),
                  )
                }
                rows={2}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              />
              <textarea
                aria-label={`Answer outline ${qi + 1}`}
                value={edit.outline}
                onChange={(e) =>
                  setEdits(
                    edits.map((item, i) =>
                      i !== qi ? item : { ...item, outline: e.target.value },
                    ),
                  )
                }
                rows={4}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              />
            </div>
          ))}
          <SaveRow saving={saving} onSave={() => void handleSave()} />
        </div>
      ) : (
        <div>
          {content.questions.map((question, qi) => (
            <div
              key={qi}
              className="space-y-2 border-t border-gray-100 pt-4 first:border-t-0 first:pt-0"
            >
              <div className="flex items-start justify-between gap-3">
                <button
                  type="button"
                  aria-expanded={expanded === qi}
                  onClick={() => setExpanded(expanded === qi ? null : qi)}
                  className="text-left text-sm font-medium text-gray-900 hover:text-blue-700"
                >
                  {question.question}
                </button>
                {question.category && (
                  <span className={categoryBadgeClass(question.category)}>{question.category}</span>
                )}
              </div>
              {expanded === qi && (
                <div className="space-y-2 rounded-md bg-gray-50 p-4 text-sm text-gray-800">
                  {question.why_it_matters && (
                    <p>
                      <span className="font-medium">Why it matters:</span> {question.why_it_matters}
                    </p>
                  )}
                  {question.related_resume_area && (
                    <p>
                      <span className="font-medium">Related resume area:</span>{' '}
                      {question.related_resume_area}
                    </p>
                  )}
                  {question.answer_outline.length > 0 && (
                    <ul className="list-inside list-disc space-y-1">
                      {question.answer_outline.map((point, pi) => (
                        <li key={pi}>{point}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

// --- 頁面 -----------------------------------------------------------------------

export default function ApplicationKitPage() {
  const { jobId } = useParams<{ jobId: string }>()

  const [job, setJob] = useState<Job | null>(null)
  const [resume, setResume] = useState<Resume | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [kit, setKit] = useState<ApplicationKitResponse | null>(null)
  // kit GET 失敗 ≠ kit 不存在：不能誤顯示「尚未生成」讓使用者白跑一次 agent。
  const [kitLoadFailed, setKitLoadFailed] = useState(false)
  const [generating, setGenerating] = useState(false)

  useEffect(() => {
    if (!jobId) return
    let ignore = false
    // job / resume 平行載入；有履歷才查既有 kit（GET 需要 resume_id）。
    Promise.all([fetchJob(jobId), fetchCurrentResume()])
      .then(async ([j, r]) => {
        if (ignore) return
        setJob(j)
        setResume(r)
        if (j && r) {
          try {
            const existing = await fetchApplicationKit(j.id, r.id)
            if (!ignore) setKit(existing)
          } catch {
            if (!ignore) setKitLoadFailed(true)
          }
        }
      })
      .catch(() => {
        if (!ignore) setError('Failed to load the page. Reload to retry.')
      })
      .finally(() => {
        if (!ignore) setLoading(false)
      })
    return () => {
      ignore = true
    }
  }, [jobId])

  async function handleGenerate() {
    if (!job) return
    setError(null)
    setGenerating(true)
    try {
      // 不帶 resume_id → 後端用 current resume（規劃定案）。
      setKit(await generateApplicationKit(job.id))
    } catch (e) {
      const detail =
        axios.isAxiosError(e) && e.response?.status === 409
          ? (e.response.data as { detail?: string } | undefined)?.detail
          : undefined
      setError(detail ?? 'Failed to generate the application kit. Please try again.')
    } finally {
      setGenerating(false)
    }
  }

  // 卡片存檔後以新版本 artifact 就地取代（missing/errors 不變）。
  function replaceArtifact<K extends 'tailored_resume' | 'cover_letter' | 'interview_prep'>(
    kind: K,
    artifact: NonNullable<ApplicationKitResponse[K]>,
  ) {
    setKit((prev) => (prev ? { ...prev, [kind]: artifact } : prev))
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-4">
          <h1 className="text-xl font-bold text-gray-900">Application kit</h1>
          <Link to={jobId ? `/jobs/${jobId}` : '/jobs'} className="text-sm text-blue-600 hover:underline">
            ← Job detail
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
        ) : !resume ? (
          <p className="text-sm text-gray-600">
            Upload a resume first to build an application kit.{' '}
            <Link to="/resume" className="text-blue-600 hover:underline">
              Go to resume page
            </Link>
          </p>
        ) : kitLoadFailed ? (
          <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">
            Failed to load the existing application kit. Reload the page to retry.
          </div>
        ) : (
          <>
            {/* 標頭卡：目標職缺 + match 分數 + 生成按鈕 */}
            <section className="space-y-3 rounded-lg bg-white p-6 shadow">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-lg font-semibold text-gray-900">
                    {job.title ?? 'Untitled job'}
                  </h2>
                  <p className="text-sm text-gray-500">{job.company}</p>
                </div>
                {kit?.match_score != null && (
                  <span className={scoreBadgeClass(kit.match_score)}>
                    Match {(kit.match_score * 100).toFixed(0)}%
                  </span>
                )}
              </div>

              {job.index_status !== 'indexed' && (
                <div className="rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
                  This job is not indexed for retrieval, so the kit cannot be generated. Re-add
                  the job to rebuild its index.
                </div>
              )}

              <div className="space-y-1">
                <button
                  type="button"
                  onClick={() => void handleGenerate()}
                  disabled={generating || job.index_status !== 'indexed'}
                  className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {generating
                    ? 'Generating… (30–90s)'
                    : kit
                      ? 'Regenerate kit'
                      : 'Generate application kit'}
                </button>
                <p className="text-xs text-gray-400">
                  The AI agent computes your match, gathers job evidence, and writes all three
                  artifacts — this takes a while.
                </p>
              </div>

              {kit && kit.missing.length > 0 && (
                <div className="rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
                  Some artifacts could not be generated: {kit.missing.join(', ')}. Regenerate to
                  try again.
                </div>
              )}
              {kit && kit.errors.length > 0 && (
                <p className="text-xs text-gray-400">{kit.errors.join(' · ')}</p>
              )}
            </section>

            {!kit ? (
              <p className="text-sm text-gray-400">
                No kit yet. Generate one to get resume suggestions, a cover letter, and interview
                prep for this job.
              </p>
            ) : (
              <>
                {/* key=artifact.id：重生成後卡片整個重掛，殘留的編輯草稿
                    不會蓋到新產物。 */}
                {kit.tailored_resume && (
                  <TailoredResumeCard
                    key={kit.tailored_resume.id}
                    artifact={kit.tailored_resume}
                    onSaved={(a) => replaceArtifact('tailored_resume', a)}
                    onError={setError}
                  />
                )}
                {kit.cover_letter && (
                  <CoverLetterCard
                    key={kit.cover_letter.id}
                    artifact={kit.cover_letter}
                    onSaved={(a) => replaceArtifact('cover_letter', a)}
                    onError={setError}
                  />
                )}
                {kit.interview_prep && (
                  <InterviewPrepCard
                    key={kit.interview_prep.id}
                    artifact={kit.interview_prep}
                    onSaved={(a) => replaceArtifact('interview_prep', a)}
                    onError={setError}
                  />
                )}
              </>
            )}
          </>
        )}
      </main>
    </div>
  )
}
