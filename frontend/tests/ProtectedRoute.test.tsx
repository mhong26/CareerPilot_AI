import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import ProtectedRoute from '../src/components/ProtectedRoute'
import type { AuthContextValue } from '../src/context/auth-context'
import { useAuth } from '../src/hooks/useAuth'

vi.mock('../src/hooks/useAuth')

const baseAuth: AuthContextValue = {
  user: null,
  isLoading: false,
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
}

function renderWithAuth(value: AuthContextValue) {
  vi.mocked(useAuth).mockReturnValue(value)
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <Routes>
        <Route element={<ProtectedRoute />}>
          <Route path="/dashboard" element={<div>Dashboard Content</div>} />
        </Route>
        <Route path="/login" element={<div>Login Screen</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

test('redirects to /login when unauthenticated', () => {
  renderWithAuth({ ...baseAuth, user: null })
  expect(screen.getByText('Login Screen')).toBeInTheDocument()
})

test('renders protected content when authenticated', () => {
  renderWithAuth({
    ...baseAuth,
    user: {
      id: '1',
      email: 'a@b.com',
      full_name: null,
      is_active: true,
      created_at: '2026-01-01T00:00:00Z',
    },
  })
  expect(screen.getByText('Dashboard Content')).toBeInTheDocument()
})
