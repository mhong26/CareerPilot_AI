import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import * as jobApi from '../src/api/job'
import * as resumeApi from '../src/api/resume'
import * as skillGapApi from '../src/api/skillGap'
import JobDetailPage from '../src/pages/JobDetailPage'
import type { Job } from '../src/types/job'
import type { Resume } from '../src/types/resume'
import type { SkillGapReport } from '../src/types/skillGap'

vi.mock('../src/api/job')
vi.mock('../src/api/resume')
vi.mock('../src/api/skillGap')

const sampleJob: Job = {
  id: 'j1',
  company: 'Acme',
  title: 'Backend Engineer',
  parse_status: 'parsed',
  parse_error: null,
  index_status: 'indexed',
  index_error: null,
  created_at: '2026-07-01T00:00:00Z',
  raw_text: 'raw job text',
  chunk_count: 2,
  parsed_data: {
    company: 'Acme',
    title: 'Backend Engineer',
    location: 'Taipei',
    work_mode: 'Hybrid',
    responsibilities: ['Build APIs'],
    required_skills: ['Python', 'Go'],
    preferred_skills: [],
    qualifications: [],
    experience_requirements: [],
  },
}

const sampleResume: Resume = {
  id: 'r1',
  source_filename: null,
  source_type: 'text',
  parse_status: 'parsed',
  parse_error: null,
  created_at: '2026-06-01T00:00:00Z',
  current_version_number: 1,
  parsed_data: null,
}

const sampleReport: SkillGapReport = {
  id: 'g1',
  resume_id: 'r1',
  resume_version_number: 1,
  job_id: 'j1',
  retrieval: {
    query_kind: 'skills',
    top_k: 5,
    chunks: [
      {
        chunk_id: 'c1',
        chunk_index: 1,
        section: 'required_skills',
        cosine_similarity: 1.0,
        rerank_score: 2.0,
      },
    ],
    ranked_chunk_ids: ['c1'],
    rerank_used: true,
    rerank_model: 'cross-encoder/ms-marco-MiniLM-L-6-v2',
    rerank_error: null,
  },
  analysis: {
    gaps: [
      {
        skill: 'Go',
        severity: 'high',
        reason: 'The job requires Go experience.',
        evidence_chunk_ids: ['c1'],
        suggestion: 'Build a small service in Go.',
      },
    ],
    overall_summary: 'Solid base with one language gap.',
    dropped_gap_count: 0,
  },
  generation_error: null,
  chunks: [{ id: 'c1', section: 'required_skills', content: 'Required skills: Python, Go' }],
  created_at: '2026-07-02T00:00:00Z',
  updated_at: '2026-07-02T00:00:00Z',
}

// 生成失敗的降級報告：analysis null、generation_error 有原因、檢索證據仍在。
const failedReport: SkillGapReport = {
  ...sampleReport,
  analysis: null,
  generation_error: 'StructuredOutputError: gap gen down',
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(jobApi.fetchJob).mockResolvedValue(sampleJob)
  vi.mocked(resumeApi.fetchCurrentResume).mockResolvedValue(sampleResume)
  vi.mocked(skillGapApi.fetchSkillGapForJob).mockResolvedValue(sampleReport)
})

// useParams 需要真實 route match（同 ProtectedRoute.test 的 Routes 包法）。
function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/jobs/j1']}>
      <Routes>
        <Route path="/jobs/:jobId" element={<JobDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

test('switching to the skill gap tab shows gaps with severity badges', async () => {
  renderPage()
  await screen.findByText('Backend Engineer')
  fireEvent.click(screen.getByRole('tab', { name: 'Skill gap analysis' }))
  expect(await screen.findByText('Go')).toBeInTheDocument()
  expect(screen.getByText('high')).toBeInTheDocument()
  expect(screen.getByText('Solid base with one language gap.')).toBeInTheDocument()
  // details 內容被切走
  expect(screen.queryByText('Parsed sections')).not.toBeInTheDocument()
})

test('expanding a citation shows the source chunk content', async () => {
  renderPage()
  await screen.findByText('Backend Engineer')
  fireEvent.click(screen.getByRole('tab', { name: 'Skill gap analysis' }))
  await screen.findByText('Go')

  const evidenceButton = screen.getByRole('button', { name: 'Evidence: Required skills' })
  expect(screen.queryByText('Required skills: Python, Go')).not.toBeInTheDocument()
  fireEvent.click(evidenceButton)
  expect(screen.getByText('Required skills: Python, Go')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Hide evidence' })).toBeInTheDocument()
})

test('a report with generation_error shows the warning banner and evidence only', async () => {
  vi.mocked(skillGapApi.fetchSkillGapForJob).mockResolvedValue(failedReport)
  renderPage()
  await screen.findByText('Backend Engineer')
  fireEvent.click(screen.getByRole('tab', { name: 'Skill gap analysis' }))

  expect(await screen.findByText(/gap generation failed/i)).toBeInTheDocument()
  // 降級：證據照列、gap 卡片不出現
  expect(screen.getByText('Required skills: Python, Go')).toBeInTheDocument()
  expect(screen.queryByText('Go')).not.toBeInTheDocument()
})

test('a non-404 resume failure shows a load error, not the upload hint', async () => {
  vi.mocked(resumeApi.fetchCurrentResume).mockRejectedValue(new Error('network down'))
  renderPage()
  await screen.findByText('Backend Engineer')
  fireEvent.click(screen.getByRole('tab', { name: 'Skill gap analysis' }))

  expect(await screen.findByText(/failed to load your resume/i)).toBeInTheDocument()
  // 不得誤導使用者去重新上傳履歷（404 才代表沒有履歷）
  expect(screen.queryByText(/upload a resume first/i)).not.toBeInTheDocument()
})

test('generate button shows a pending state while analyzing', async () => {
  vi.mocked(skillGapApi.fetchSkillGapForJob).mockResolvedValue(null)
  vi.mocked(skillGapApi.generateSkillGap).mockReturnValue(new Promise(() => {})) // 永不 resolve
  renderPage()
  await screen.findByText('Backend Engineer')
  fireEvent.click(screen.getByRole('tab', { name: 'Skill gap analysis' }))

  const button = await screen.findByRole('button', { name: 'Analyze skill gaps' })
  fireEvent.click(button)
  expect(await screen.findByRole('button', { name: 'Analyzing…' })).toBeDisabled()
  expect(skillGapApi.generateSkillGap).toHaveBeenCalledWith('r1', 'j1')
})
