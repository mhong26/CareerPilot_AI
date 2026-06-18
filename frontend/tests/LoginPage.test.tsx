import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

import type { AuthContextValue } from '../src/context/auth-context'
import { useAuth } from '../src/hooks/useAuth'
import LoginPage from '../src/pages/LoginPage'

vi.mock('../src/hooks/useAuth')

test('submits credentials through auth.login', async () => {
  const login = vi.fn().mockResolvedValue(undefined)
  const value: AuthContextValue = {
    user: null,
    isLoading: false,
    login,
    register: vi.fn(),
    logout: vi.fn(),
  }
  vi.mocked(useAuth).mockReturnValue(value)

  render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  )

  fireEvent.change(screen.getByLabelText('Email'), {
    target: { value: 'user@example.com' },
  })
  fireEvent.change(screen.getByLabelText('Password'), {
    target: { value: 'Sup3rSecret!' },
  })
  fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

  await waitFor(() => {
    expect(login).toHaveBeenCalledWith('user@example.com', 'Sup3rSecret!')
  })
})
