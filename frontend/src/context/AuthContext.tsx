import { useEffect, useState, type ReactNode } from 'react'

import { fetchMe, loginRequest, logoutRequest, registerRequest } from '../api/auth'
import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from '../lib/auth-storage'
import type { User } from '../types/auth'
import { AuthContext } from './auth-context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  // Restore the session on first load if an access token is stored (FR-6).
  useEffect(() => {
    if (!getAccessToken()) {
      setIsLoading(false)
      return
    }
    fetchMe()
      .then(setUser)
      .catch(() => clearTokens())
      .finally(() => setIsLoading(false))
  }, [])

  async function login(email: string, password: string) {
    const tokens = await loginRequest({ email, password })
    setTokens(tokens.access_token, tokens.refresh_token)
    setUser(await fetchMe())
  }

  async function register(email: string, password: string, fullName?: string) {
    await registerRequest({ email, password, full_name: fullName ?? null })
    await login(email, password)
  }

  async function logout() {
    const refreshToken = getRefreshToken()
    if (refreshToken) {
      try {
        await logoutRequest(refreshToken)
      } catch {
        // best-effort server-side revoke; we clear locally regardless
      }
    }
    clearTokens()
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, isLoading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
