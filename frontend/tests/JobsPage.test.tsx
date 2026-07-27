import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import * as jobApi from '../src/api/job'
import JobsPage from '../src/pages/JobsPage'
import type { Job, JobListItem } from '../src/types/job'

vi.mock('../src/api/job')

const sampleListItem: JobListItem = {
  id: 'j1',
  company: 'Acme Corp',
  title: 'Backend Engineer',
  parse_status: 'parsed',
  index_status: 'indexed',
  created_at: '2026-07-20T00:00:00Z',
}

const sampleJob: Job = {
  id: 'j1',
  company: 'Acme Corp',
  title: 'Backend Engineer',
  parse_status: 'parsed',
  parse_error: null,
  index_status: 'indexed',
  index_error: null,
  created_at: '2026-07-20T00:00:00Z',
  raw_text: 'A long job description.',
  chunk_count: 4,
  parsed_data: {
    company: 'Acme Corp',
    title: 'Backend Engineer',
    location: 'Remote',
    work_mode: 'remote',
    responsibilities: ['Build APIs'],
    required_skills: ['Python'],
    preferred_skills: [],
    qualifications: [],
    experience_requirements: [],
  },
}

beforeEach(() => {
  vi.mocked(jobApi.fetchJobs).mockResolvedValue([sampleListItem])
  vi.mocked(jobApi.createJob).mockResolvedValue(sampleJob)
  vi.mocked(jobApi.deleteJob).mockResolvedValue()
})

test('loads and lists jobs', async () => {
  render(
    <MemoryRouter>
      <JobsPage />
    </MemoryRouter>,
  )

  expect(await screen.findByText('Backend Engineer')).toBeInTheDocument()
  expect(screen.getByText('· Acme Corp')).toBeInTheDocument()
  expect(screen.getByText('indexed')).toBeInTheDocument()
})

test('pasting text and submitting calls createJob with the text', async () => {
  render(
    <MemoryRouter>
      <JobsPage />
    </MemoryRouter>,
  )

  fireEvent.change(screen.getByPlaceholderText(/paste a job description/i), {
    target: { value: 'Backend Engineer at Acme. Python required.' },
  })
  fireEvent.click(screen.getByRole('button', { name: /add & parse/i }))

  await waitFor(() => {
    expect(jobApi.createJob).toHaveBeenCalledWith('Backend Engineer at Acme. Python required.')
  })
})

test('shows a warning when indexing failed', async () => {
  vi.mocked(jobApi.createJob).mockResolvedValue({
    ...sampleJob,
    index_status: 'failed',
    index_error: 'quota exceeded',
  })

  render(
    <MemoryRouter>
      <JobsPage />
    </MemoryRouter>,
  )

  fireEvent.change(screen.getByPlaceholderText(/paste a job description/i), {
    target: { value: 'Some job description text.' },
  })
  fireEvent.click(screen.getByRole('button', { name: /add & parse/i }))

  expect(await screen.findByText(/embedding indexing failed/i)).toBeInTheDocument()
})
