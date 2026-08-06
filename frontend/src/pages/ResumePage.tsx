import { type FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  fetchCurrentResume,
  fetchVersions,
  updateResume,
  uploadResumeFile,
  uploadResumeText,
} from '../api/resume'
import ResumeEditor from '../components/resume/ResumeEditor'
import ElapsedTimer from '../components/ui/ElapsedTimer'
import ErrorBanner from '../components/ui/ErrorBanner'
import Spinner from '../components/ui/Spinner'
import WarningBanner from '../components/ui/WarningBanner'
import { getErrorMessage } from '../lib/errors'
import { type Resume, type ResumeParsed, type ResumeVersion, emptyResumeParsed } from '../types/resume'

// 對齊後端限制：max_upload_size_mb=10、貼文最短 10 字（_MIN_CHARS）。
const MAX_FILE_MB = 10
const MIN_TEXT_CHARS = 10

export default function ResumePage() {
  const [resume, setResume] = useState<Resume | null>(null)
  const [draft, setDraft] = useState<ResumeParsed | null>(null)
  const [versions, setVersions] = useState<ResumeVersion[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [versionsError, setVersionsError] = useState(false)

  const [uploadMode, setUploadMode] = useState<'file' | 'text'>('text')
  const [file, setFile] = useState<File | null>(null)
  const [text, setText] = useState('')

  // 載入目前履歷（mount 時一次）。
  useEffect(() => {
    fetchCurrentResume()
      .then((r) => applyResume(r))
      .catch((e) => setError(getErrorMessage(e, 'Failed to load your resume.')))
      .finally(() => setLoading(false))
  }, [])

  function applyResume(r: Resume | null) {
    setResume(r)
    setDraft(r ? (r.parsed_data ?? emptyResumeParsed()) : null)
    setVersionsError(false)
    if (r) {
      fetchVersions(r.id)
        .then(setVersions)
        .catch(() => {
          // 版本清單載入失敗要現形，不能與「尚無版本」混為一談。
          setVersions([])
          setVersionsError(true)
        })
    } else {
      setVersions([])
    }
  }

  async function handleUpload(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (uploadMode === 'file' && file && file.size > MAX_FILE_MB * 1024 * 1024) {
      setError(`File is larger than ${MAX_FILE_MB} MB. Please upload a smaller file.`)
      return
    }
    if (uploadMode === 'text' && text.trim().length < MIN_TEXT_CHARS) {
      setError(`Please paste at least ${MIN_TEXT_CHARS} characters of resume text.`)
      return
    }
    setUploading(true)
    try {
      const r =
        uploadMode === 'file'
          ? await uploadResumeFile(file as File)
          : await uploadResumeText(text)
      applyResume(r)
      setText('')
      setFile(null)
    } catch (e) {
      setError(
        getErrorMessage(e, 'Upload failed. Check the file type (PDF/DOCX) or text, then try again.'),
      )
    } finally {
      setUploading(false)
    }
  }

  async function handleSave() {
    if (!resume || !draft) return
    setError(null)
    setSaving(true)
    try {
      const r = await updateResume(resume.id, draft)
      applyResume(r)
    } catch (e) {
      setError(getErrorMessage(e, 'Failed to save. Please try again.'))
    } finally {
      setSaving(false)
    }
  }

  const canUpload = uploadMode === 'file' ? file !== null : text.trim().length > 0

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-4">
          <h1 className="text-xl font-bold text-gray-900">Resume</h1>
          <Link to="/dashboard" className="text-sm text-blue-600 hover:underline">
            ← Dashboard
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl space-y-6 px-4 py-8">
        {error && <ErrorBanner message={error} />}

        {/* 上傳區 */}
        <section className="space-y-4 rounded-lg bg-white p-6 shadow">
          <h2 className="text-lg font-semibold text-gray-900">Upload a resume</h2>
          <div className="flex gap-4 text-sm">
            <label className="flex items-center gap-1">
              <input type="radio" checked={uploadMode === 'text'} onChange={() => setUploadMode('text')} />
              Paste text
            </label>
            <label className="flex items-center gap-1">
              <input type="radio" checked={uploadMode === 'file'} onChange={() => setUploadMode('file')} />
              Upload file
            </label>
          </div>
          <form onSubmit={handleUpload} className="space-y-3">
            {uploadMode === 'text' ? (
              <textarea
                value={text}
                onChange={(event) => setText(event.target.value)}
                rows={6}
                placeholder="Paste your resume text here…"
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
              />
            ) : (
              <input
                type="file"
                accept=".pdf,.docx"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="block w-full text-sm text-gray-600"
              />
            )}
            <button
              type="submit"
              disabled={uploading || !canUpload}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {uploading ? 'Uploading & parsing…' : 'Upload & parse'}
            </button>
            {uploading && <ElapsedTimer hint="parsing with AI usually takes a few seconds" />}
          </form>
        </section>

        {loading ? (
          <Spinner label="Loading your resume…" />
        ) : resume && draft ? (
          <>
            {resume.parse_status === 'failed' && (
              <WarningBanner
                message={`Automatic parsing failed${resume.parse_error ? `: ${resume.parse_error}` : ''}. Please fill in the fields manually and save.`}
              />
            )}

            {/* 編輯器 */}
            <section className="space-y-5 rounded-lg bg-white p-6 shadow">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-gray-900">Edit resume</h2>
                <button
                  type="button"
                  onClick={() => void handleSave()}
                  disabled={saving}
                  className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {saving ? 'Saving…' : 'Save new version'}
                </button>
              </div>
              <ResumeEditor value={draft} onChange={setDraft} />
            </section>

            {/* 版本歷史（唯讀） */}
            <section className="space-y-3 rounded-lg bg-white p-6 shadow">
              <h2 className="text-lg font-semibold text-gray-900">Version history</h2>
              {versionsError && (
                <WarningBanner message="Could not load version history. Reload the page to retry." />
              )}
              {versions.length === 0 ? (
                !versionsError && <p className="text-sm text-gray-400">No versions yet.</p>
              ) : (
                <ul className="divide-y divide-gray-100 text-sm">
                  {[...versions].reverse().map((v) => (
                    <li key={v.id} className="flex items-center justify-between py-2">
                      <span className="font-medium text-gray-800">
                        v{v.version_number} · {v.label}
                      </span>
                      <span className="text-gray-400">
                        {new Date(v.created_at).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        ) : (
          <p className="text-center text-gray-400">
            No resume yet. Upload one above to get started.
          </p>
        )}
      </main>
    </div>
  )
}
