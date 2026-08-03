import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as kitApi from '../src/api/applicationKit'
import * as jobApi from '../src/api/job'
import * as resumeApi from '../src/api/resume'
import ApplicationKitPage from '../src/pages/ApplicationKitPage'
import type { ApplicationKitResponse } from '../src/types/applicationKit'
import type { Job } from '../src/types/job'
import type { Resume } from '../src/types/resume'

vi.mock('../src/api/applicationKit')
vi.mock('../src/api/job')
vi.mock('../src/api/resume')

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
  parsed_data: null,
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

const sampleKit: ApplicationKitResponse = {
  job_id: 'j1',
  resume_id: 'r1',
  match_score: 0.75,
  tailored_resume: {
    id: 'a1',
    kind: 'tailored_resume',
    source: 'agent',
    version_number: 1,
    run_id: 'run1',
    resume_version_number: 1,
    content: {
      overall_strategy: 'Lead with backend work.',
      section_suggestions: [
        {
          section: 'experience',
          bullet_rewrites: [{ original: 'Built APIs', improved: 'Built fast APIs', reason: '' }],
          keywords_to_add: ['Python'],
          note: '',
        },
      ],
      top_keywords: ['Python'],
    },
    created_at: '2026-07-02T00:00:00Z',
  },
  cover_letter: {
    id: 'a2',
    kind: 'cover_letter',
    source: 'agent',
    version_number: 1,
    run_id: 'run1',
    resume_version_number: 1,
    content: { intro: 'Dear team,', body_paragraphs: ['I built APIs.'], closing: 'Thanks.' },
    created_at: '2026-07-02T00:00:00Z',
  },
  interview_prep: {
    id: 'a3',
    kind: 'interview_prep',
    source: 'agent',
    version_number: 1,
    run_id: 'run1',
    resume_version_number: 1,
    content: {
      questions: [
        {
          question: 'Why us?',
          category: 'behavioral',
          why_it_matters: 'Motivation.',
          related_resume_area: 'Summary',
          answer_outline: ['Mention mission'],
        },
      ],
    },
    created_at: '2026-07-02T00:00:00Z',
  },
  missing: [],
  errors: [],
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/jobs/j1/application-kit']}>
      <Routes>
        <Route path="/jobs/:jobId/application-kit" element={<ApplicationKitPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(jobApi.fetchJob).mockResolvedValue(sampleJob)
  vi.mocked(resumeApi.fetchCurrentResume).mockResolvedValue(sampleResume)
})

describe('ApplicationKitPage', () => {
  it('shows empty state and disables the button while generating', async () => {
    vi.mocked(kitApi.fetchApplicationKit).mockResolvedValue(null)
    // pending promise → generating 狀態可觀察。
    vi.mocked(kitApi.generateApplicationKit).mockReturnValue(new Promise(() => {}))
    renderPage()

    const button = await screen.findByRole('button', { name: 'Generate application kit' })
    expect(screen.getByText(/No kit yet/)).toBeInTheDocument()

    fireEvent.click(button)
    expect(await screen.findByRole('button', { name: /Generating…/ })).toBeDisabled()
    expect(kitApi.generateApplicationKit).toHaveBeenCalledWith('j1')
  })

  it('renders all three artifact cards with match score', async () => {
    vi.mocked(kitApi.fetchApplicationKit).mockResolvedValue(sampleKit)
    renderPage()

    expect(await screen.findByText('Resume suggestions')).toBeInTheDocument()
    expect(screen.getByText('Cover letter')).toBeInTheDocument()
    expect(screen.getByText('Interview prep')).toBeInTheDocument()
    expect(screen.getByText('Match 75%')).toBeInTheDocument()
    expect(screen.getByText('Dear team,')).toBeInTheDocument()
    expect(screen.getByText('Built fast APIs')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Regenerate kit' })).toBeInTheDocument()
  })

  it('warns when some artifacts are missing', async () => {
    vi.mocked(kitApi.fetchApplicationKit).mockResolvedValue({
      ...sampleKit,
      cover_letter: null,
      missing: ['cover_letter'],
    })
    renderPage()

    expect(
      await screen.findByText(/Some artifacts could not be generated: cover_letter/),
    ).toBeInTheDocument()
    expect(screen.queryByText('Cover letter')).not.toBeInTheDocument()
  })

  it('shows a reload message instead of the generate button when the kit GET fails', async () => {
    vi.mocked(kitApi.fetchApplicationKit).mockRejectedValue(new Error('network down'))
    renderPage()

    expect(
      await screen.findByText(/Failed to load the existing application kit/),
    ).toBeInTheDocument()
    // 不能誤顯示「尚未生成」讓使用者白跑一次 agent。
    expect(screen.queryByRole('button', { name: 'Generate application kit' })).toBeNull()
  })

  it('keeps raw spaces and newlines while editing an answer outline', async () => {
    vi.mocked(kitApi.fetchApplicationKit).mockResolvedValue(sampleKit)
    vi.mocked(kitApi.updateArtifact).mockResolvedValue({
      ...sampleKit.interview_prep!,
      id: 'a3-v2',
      source: 'edit',
      version_number: 2,
    })
    renderPage()

    await screen.findByText('Interview prep')
    fireEvent.click(screen.getAllByRole('button', { name: 'Edit' })[2])
    const outline = screen.getByLabelText('Answer outline 1')
    // 打字途中包含空行與尾端空白：textarea 必須原樣保留（split 只在存檔時做）。
    fireEvent.change(outline, { target: { value: 'Point A\n\nPoint B ' } })
    expect(outline).toHaveValue('Point A\n\nPoint B ')

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await screen.findByText('v2 · edited')
    expect(kitApi.updateArtifact).toHaveBeenCalledWith('a3', {
      questions: [
        expect.objectContaining({ answer_outline: ['Point A', 'Point B'] }),
      ],
    })
  })

  it('saves an edited cover letter as a new version', async () => {
    vi.mocked(kitApi.fetchApplicationKit).mockResolvedValue(sampleKit)
    vi.mocked(kitApi.updateArtifact).mockResolvedValue({
      ...sampleKit.cover_letter!,
      id: 'a2-v2',
      source: 'edit',
      version_number: 2,
      content: { intro: 'Hi there,', body_paragraphs: ['I built APIs.'], closing: 'Thanks.' },
    })
    renderPage()

    await screen.findByText('Cover letter')
    // 三張卡各有一個 Edit：順序 tailored → cover → prep。
    fireEvent.click(screen.getAllByRole('button', { name: 'Edit' })[1])
    const intro = screen.getByLabelText('Intro')
    fireEvent.change(intro, { target: { value: 'Hi there,' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(await screen.findByText('v2 · edited')).toBeInTheDocument()
    expect(kitApi.updateArtifact).toHaveBeenCalledWith('a2', {
      intro: 'Hi there,',
      body_paragraphs: ['I built APIs.'],
      closing: 'Thanks.',
    })
    expect(screen.getByText('Hi there,')).toBeInTheDocument()
  })
})
