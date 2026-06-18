import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'

import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from '../lib/auth-storage'
import type { AuthTokens } from '../types/auth'

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export const apiClient = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

interface RetryableRequest extends InternalAxiosRequestConfig {
  _retry?: boolean
}

// Public auth endpoints whose 401s must NOT trigger a token refresh.
const AUTH_PATHS = ['/auth/login', '/auth/register', '/auth/refresh']

// Attach the access token to every outgoing request.
apiClient.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

// On a 401, try the refresh token once, then replay the original request.
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as RetryableRequest | undefined
    const status = error.response?.status
    const isAuthPath = AUTH_PATHS.some((path) => original?.url?.includes(path))

    if (
      status === 401 &&
      original &&
      !original._retry &&
      !isAuthPath &&
      getRefreshToken()
    ) {
      original._retry = true
      try {
        const { data } = await axios.post<AuthTokens>(`${API_URL}/auth/refresh`, {
          refresh_token: getRefreshToken(),
        })
        setTokens(data.access_token, data.refresh_token)
        original.headers.set('Authorization', `Bearer ${data.access_token}`)
        return apiClient(original)
      } catch (refreshError) {
        clearTokens()
        if (window.location.pathname !== '/login') {
          window.location.href = '/login'
        }
        return Promise.reject(refreshError)
      }
    }

    return Promise.reject(error)
  },
)

export const checkHealth = () => apiClient.get('/health')
