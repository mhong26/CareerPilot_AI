import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'

import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from '../lib/auth-storage'
import type { AuthTokens } from '../types/auth'

// dev 由 VITE_API_URL 直連 backend；production build 不設此變數，
// 落到相對路徑 /api，由 nginx 反向代理到 backend（同源，無 CORS）。
const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export const apiClient = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  // 一般請求 30s 上限；長任務（match / skill gap / kit / upload+parse）
  // 於各 api module 以 per-request LONG_TASK_TIMEOUT_MS 覆寫。
  timeout: 30_000,
})

/** LLM-heavy 長任務的 per-request timeout（kit 生成最長 30–90s）。 */
export const LONG_TASK_TIMEOUT_MS = 180_000

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

// Single-flight refresh：refresh token 是單次使用（rotate），多個請求同時 401
// 時若並行重刷，後到者會拿已被輪替的舊 token 直接失敗並把使用者登出；
// 以共享 promise 保證同時間只有一次 /auth/refresh，其餘請求等同一結果。
let refreshPromise: Promise<AuthTokens> | null = null

const refreshTokens = (): Promise<AuthTokens> => {
  refreshPromise ??= axios
    .post<AuthTokens>(`${API_URL}/auth/refresh`, {
      refresh_token: getRefreshToken(),
    })
    .then(({ data }) => {
      setTokens(data.access_token, data.refresh_token)
      return data
    })
    .finally(() => {
      refreshPromise = null
    })
  return refreshPromise
}

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
        const { access_token } = await refreshTokens()
        original.headers.set('Authorization', `Bearer ${access_token}`)
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
