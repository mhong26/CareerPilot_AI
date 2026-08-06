import { AxiosError } from 'axios'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import type { AuthContextValue } from '../src/context/auth-context'
import { useAuth } from '../src/hooks/useAuth'
import RegisterPage from '../src/pages/RegisterPage'

vi.mock('../src/hooks/useAuth')

const registerMock = vi.fn()

beforeEach(() => {
  registerMock.mockReset()
  const value: AuthContextValue = {
    user: null,
    isLoading: false,
    login: vi.fn(),
    register: registerMock,
    logout: vi.fn(),
  }
  vi.mocked(useAuth).mockReturnValue(value)
})

function fillAndSubmit(password: string) {
  fireEvent.change(screen.getByLabelText('Email'), {
    target: { value: 'new@example.com' },
  })
  fireEvent.change(screen.getByLabelText('Password'), {
    target: { value: password },
  })
  fireEvent.click(screen.getByRole('button', { name: /create account/i }))
}

test('blocks a too-short password client-side without calling the API', async () => {
  render(
    <MemoryRouter>
      <RegisterPage />
    </MemoryRouter>,
  )

  fillAndSubmit('short')

  await waitFor(() => {
    expect(screen.getByText(/password must be 8–72 characters/i)).toBeInTheDocument()
  })
  expect(registerMock).not.toHaveBeenCalled()
})

test('shows the backend 409 detail when the email is taken', async () => {
  const err = new AxiosError('Request failed')
  err.response = {
    status: 409,
    data: { detail: 'Email already registered' },
    statusText: '',
    headers: {},
    config: {},
  } as AxiosError['response']
  registerMock.mockRejectedValue(err)

  render(
    <MemoryRouter>
      <RegisterPage />
    </MemoryRouter>,
  )

  fillAndSubmit('Sup3rSecret!')

  await waitFor(() => {
    expect(screen.getByText('Email already registered')).toBeInTheDocument()
  })
})
