import { AxiosError } from 'axios'
import { expect, test } from 'vitest'

import { getErrorMessage } from '../src/lib/errors'

function axiosErrorWith(status: number, data: unknown): AxiosError {
  const err = new AxiosError('Request failed')
  // AxiosError.response 需要完整型別，測試只用到 status / data。
  err.response = {
    status,
    data,
    statusText: '',
    headers: {},
    config: {},
  } as AxiosError['response']
  return err
}

test('returns the backend detail string as-is', () => {
  const err = axiosErrorWith(409, { detail: 'Resume has not been parsed yet.' })
  expect(getErrorMessage(err)).toBe('Resume has not been parsed yet.')
})

test('joins FastAPI 422 validation messages', () => {
  const err = axiosErrorWith(422, {
    detail: [
      { msg: 'String should have at most 72 characters', loc: ['body', 'password'] },
      { msg: 'value is not a valid email address', loc: ['body', 'email'] },
    ],
  })
  expect(getErrorMessage(err)).toBe(
    'String should have at most 72 characters; value is not a valid email address',
  )
})

test('falls back to a status-specific message when detail is missing', () => {
  expect(getErrorMessage(axiosErrorWith(429, {}))).toMatch(/too many requests/i)
  expect(getErrorMessage(axiosErrorWith(503, {}))).toMatch(/temporarily unavailable/i)
})

test('detects network errors (no response)', () => {
  const err = new AxiosError('Network Error')
  expect(getErrorMessage(err)).toMatch(/network error/i)
})

test('detects timeouts (ECONNABORTED)', () => {
  const err = new AxiosError('timeout exceeded', 'ECONNABORTED')
  expect(getErrorMessage(err)).toMatch(/timed out/i)
})

test('uses the caller fallback for non-axios errors', () => {
  expect(getErrorMessage(new Error('boom'), 'Custom fallback.')).toBe('Custom fallback.')
})
