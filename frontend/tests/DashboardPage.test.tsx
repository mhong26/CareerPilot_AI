import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import * as jobApi from '../src/api/job'
import * as matchApi from '../src/api/match'
import * as resumeApi from '../src/api/resume'
import { useAuth } from '../src/hooks/useAuth'
import DashboardPage from '../src/pages/DashboardPage'
import type { AuthContextValue } from '../src/context/auth-context'
import type { JobListItem } from '../src/types/job'
import type { MatchResultItem, MatchRunResponse } from '../src/types/match'
import type { Resume } from '../src/types/resume'

vi.mock('../src/api/job')
vi.mock('../src/api/match')
vi.mock('../src/api/resume')
vi.mock('../src/hooks/useAuth')

const authValue: AuthContextValue = {
  user: {
    id: 'u1',
    email: 'jane@example.com',
    full_name: 'Jane Smith',
    is_active: true,
    created_at: '2026-07-01T00:00:00Z',
  },
  isLoading: false,
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
}

const sampleResume: Resume = {
  id: 'r1',
  source_filename: null,
  source_type: 'text',
  parse_status: 'parsed',
  parse_error: null,
  created_at: '2026-07-20T00:00:00Z',
  current_version_number: 1,
  parsed_data: null,
}

const sampleJobs: JobListItem[] = [
  {
    id: 'j1',
    company: 'Acme Corp',
    title: 'Backend Engineer',
    parse_status: 'parsed',
    index_status: 'indexed',
    created_at: '2026-07-20T00:00:00Z',
  },
  {
    id: 'j2',
    company: 'Beta LLC',
    title: 'Data Scientist',
    parse_status: 'parsed',
    index_status: 'indexed',
    created_at: '2026-07-21T00:00:00Z',
  },
]

// 分數 desc：j1 80%（綠, 有 explanation）在前、j2 46%（紅, explanation 失敗）在後。
const strongMatch: MatchResultItem = {
  id: 'm1',
  job_id: 'j1',
  job_title: 'Backend Engineer',
  job_company: 'Acme Corp',
  match_score: 0.8,
  breakdown: {
    embedding_similarity: 1.0,
    required_coverage: 1.0,
    preferred_coverage: 0.0,
    experience_alignment: 0.5,
    matched_required: ['Python', 'Go'],
    missing_required: [],
    matched_preferred: [],
    missing_preferred: ['Docker'],
    equivalent_pairs: [{ job_skill: 'Go', resume_skill: 'Golang' }],
    llm_equivalence_used: true,
    resume_years: 3,
    required_years: 2,
    years_score: 1,
    title_similarity: 0,
    weights_used: {
      embedding_similarity: 0.35,
      required_coverage: 0.35,
      preferred_coverage: 0.1,
      experience_alignment: 0.2,
    },
  },
  explanation: {
    why_matched: 'Strong overlap in backend skills.',
    top_overlap: ['Python'],
    missing_skills: ['Docker'],
    risks: ['Limited preferred-skill coverage.'],
  },
  explanation_error: null,
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T00:00:00Z',
}

const weakMatch: MatchResultItem = {
  ...strongMatch,
  id: 'm2',
  job_id: 'j2',
  job_title: 'Data Scientist',
  job_company: 'Beta LLC',
  match_score: 0.46,
  explanation: null,
  explanation_error: 'explanation down',
}

const runResponse: MatchRunResponse = {
  resume_id: 'r1',
  resume_version_number: 1,
  results: [strongMatch, weakMatch],
  skipped: [],
}

beforeEach(() => {
  vi.clearAllMocks() // 清掉跨測試累積的呼叫紀錄（本檔有 not.toHaveBeenCalled 斷言）
  vi.mocked(useAuth).mockReturnValue(authValue)
  vi.mocked(resumeApi.fetchCurrentResume).mockResolvedValue(sampleResume)
  vi.mocked(jobApi.fetchJobs).mockResolvedValue(sampleJobs)
  vi.mocked(matchApi.fetchMatches).mockResolvedValue([strongMatch, weakMatch])
  vi.mocked(matchApi.runMatches).mockResolvedValue(runResponse)
})

function renderPage() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  )
}

test('loads and renders the ranked match list with score badges', async () => {
  renderPage()

  expect(await screen.findByText('Backend Engineer')).toBeInTheDocument()
  expect(screen.getByText('Data Scientist')).toBeInTheDocument()
  expect(screen.getByText('80%')).toBeInTheDocument()
  expect(screen.getByText('46%')).toBeInTheDocument()
  expect(matchApi.fetchMatches).toHaveBeenCalledWith('r1')
})

test('expanding a row shows the explanation; a null explanation shows a warning', async () => {
  renderPage()
  await screen.findByText('Backend Engineer')

  fireEvent.click(screen.getAllByRole('button', { name: 'Details' })[0])
  expect(screen.getByText('Strong overlap in backend skills.')).toBeInTheDocument()
  expect(screen.getByText('Risks')).toBeInTheDocument()
  expect(screen.getByText('Limited preferred-skill coverage.')).toBeInTheDocument()

  // 第一筆展開後其按鈕變成 "Hide"，剩下的 "Details" 即第二筆。
  // 第二筆 explanation=null → 黃色警示、分數仍顯示。
  fireEvent.click(screen.getAllByRole('button', { name: 'Details' })[0])
  expect(screen.getByText(/explanation generation failed/i)).toBeInTheDocument()
})

test('run matching calls runMatches with every job id and renders the results', async () => {
  vi.mocked(matchApi.fetchMatches).mockResolvedValue([])
  renderPage()

  fireEvent.click(await screen.findByRole('button', { name: /run matching/i }))

  await waitFor(() => {
    expect(matchApi.runMatches).toHaveBeenCalledWith('r1', ['j1', 'j2'])
  })
  expect(await screen.findByText('Backend Engineer')).toBeInTheDocument()
  expect(screen.getByText('80%')).toBeInTheDocument()
})

test('without a resume, shows the upload prompt instead of the match list', async () => {
  vi.mocked(resumeApi.fetchCurrentResume).mockResolvedValue(null)
  renderPage()

  expect(await screen.findByText('Upload a resume to start matching.')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Upload resume' })).toHaveAttribute('href', '/resume')
  expect(matchApi.fetchMatches).not.toHaveBeenCalled()
})
