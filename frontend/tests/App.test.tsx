import { render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import App from '../src/App'
import * as client from '../src/api/client'

vi.mock('../src/api/client', () => ({
  checkHealth: vi.fn(),
}))

test('renders app title', () => {
  vi.mocked(client.checkHealth).mockResolvedValue({
    data: { status: 'ok', db: 'ok', pgvector: 'ok' },
  } as never)

  render(<App />)
  expect(screen.getByText('CareerPilot AI')).toBeInTheDocument()
})

test('shows health status from backend', async () => {
  vi.mocked(client.checkHealth).mockResolvedValue({
    data: { status: 'ok', db: 'ok', pgvector: 'ok' },
  } as never)

  render(<App />)
  await waitFor(() => {
    expect(screen.getAllByText('ok').length).toBeGreaterThan(0)
  })
})
