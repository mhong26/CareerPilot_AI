import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import * as resumeApi from '../src/api/resume'
import ResumePage from '../src/pages/ResumePage'
import type { Resume } from '../src/types/resume'

vi.mock('../src/api/resume')

const sampleResume: Resume = {
  id: 'r1',
  source_filename: null,
  source_type: 'text',
  parse_status: 'parsed',
  parse_error: null,
  created_at: '2026-06-26T00:00:00Z',
  current_version_number: 1,
  parsed_data: {
    basic_info: { name: 'Jane', email: 'jane@x.com', phone: '', location: '', links: [] },
    summary: '',
    skills: ['Python', 'FastAPI'],
    experience: [],
    projects: [],
    education: [],
    certifications: [],
  },
}

beforeEach(() => {
  vi.mocked(resumeApi.fetchCurrentResume).mockResolvedValue(sampleResume)
  vi.mocked(resumeApi.fetchVersions).mockResolvedValue([
    { id: 'v1', version_number: 1, label: 'original', created_at: '2026-06-26T00:00:00Z' },
  ])
  vi.mocked(resumeApi.updateResume).mockResolvedValue({
    ...sampleResume,
    current_version_number: 2,
  })
})

test('loads and shows the current resume skills', async () => {
  render(
    <MemoryRouter>
      <ResumePage />
    </MemoryRouter>,
  )

  await waitFor(() => {
    expect(screen.getByDisplayValue('Python')).toBeInTheDocument()
    expect(screen.getByDisplayValue('FastAPI')).toBeInTheDocument()
  })
})

test('editing a skill and saving calls updateResume with the new data', async () => {
  render(
    <MemoryRouter>
      <ResumePage />
    </MemoryRouter>,
  )

  const skillInput = await screen.findByDisplayValue('FastAPI')
  fireEvent.change(skillInput, { target: { value: 'Django' } })
  fireEvent.click(screen.getByRole('button', { name: /save new version/i }))

  await waitFor(() => {
    expect(resumeApi.updateResume).toHaveBeenCalledTimes(1)
  })
  const [, parsed] = vi.mocked(resumeApi.updateResume).mock.calls[0]
  expect(parsed.skills).toContain('Django')
  expect(parsed.skills).not.toContain('FastAPI')
})

test('an oversize file is rejected client-side without calling the API', async () => {
  render(
    <MemoryRouter>
      <ResumePage />
    </MemoryRouter>,
  )
  await screen.findByDisplayValue('Python')

  fireEvent.click(screen.getByLabelText('Upload file'))
  const bigFile = new File(['x'], 'huge.pdf', { type: 'application/pdf' })
  Object.defineProperty(bigFile, 'size', { value: 11 * 1024 * 1024 })
  const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, { target: { files: [bigFile] } })
  fireEvent.click(screen.getByRole('button', { name: /upload & parse/i }))

  await waitFor(() => {
    expect(screen.getByText(/larger than 10 MB/i)).toBeInTheDocument()
  })
  expect(resumeApi.uploadResumeFile).not.toHaveBeenCalled()
})
