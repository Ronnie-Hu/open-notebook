import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import apiClient from '@/lib/api/client'
import { getApiUrl } from '@/lib/config'
import { clearTagentCsrf, setTagentAccountId, setTagentAccountMode, setTagentCsrf, tagentWriteHeaders } from '@/lib/tagent-session'

type AuthMode = 'tagent' | 'password' | 'none'

interface AuthState {
  isAuthenticated: boolean
  token: string | null
  isLoading: boolean
  error: string | null
  lastAuthCheck: number | null
  isCheckingAuth: boolean
  hasHydrated: boolean
  authRequired: boolean | null
  authMode: AuthMode | null
  authValidated: boolean
  setHasHydrated: (state: boolean) => void
  checkAuthRequired: () => Promise<boolean>
  login: (password: string, email?: string) => Promise<boolean>
  logout: () => Promise<void>
  checkAuth: (force?: boolean) => Promise<boolean>
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      isAuthenticated: false,
      token: null,
      isLoading: false,
      error: null,
      lastAuthCheck: null,
      isCheckingAuth: false,
      hasHydrated: false,
      authRequired: null,
      authMode: null,
      authValidated: false,

      setHasHydrated: (state: boolean) => {
        set({ hasHydrated: state })
      },

      checkAuthRequired: async () => {
        try {
          const response = await apiClient.get<{ auth_enabled?: boolean; mode?: AuthMode }>('/auth/status', {
            headers: { 'Cache-Control': 'no-store' },
          })

          const required = response.data.auth_enabled || false
          const mode = response.data.mode === 'tagent' ? 'tagent' : required ? 'password' : 'none'
          setTagentAccountMode(mode === 'tagent')
          set({ authRequired: required, authMode: mode, authValidated: !required,
            ...(mode === 'tagent' ? { token: null, isAuthenticated: false } : {}) })

          // If auth is not required, mark as authenticated
          if (!required) {
            set({ isAuthenticated: true, token: 'not-required' })
          }

          return required
        } catch (error) {
          console.error('Failed to check auth status:', error)

          // A failed status check cannot establish which login mode is active.
          // Keep the form blocked until a retry succeeds, including on HTTP 503.
          set({
            error: 'Unable to determine login mode. Please retry the connection.',
            authRequired: null,
            authMode: null,
            authValidated: false,
            isAuthenticated: false,
          })

          // Re-throw the error so the UI can handle it
          throw error
        }
      },

      login: async (password: string, email?: string) => {
        set({ isLoading: true, error: null })
        try {
          if (get().authMode === 'tagent') {
            const response = await fetch('/agent-api/auth/login', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ identifier: email, password }),
            })
            if (!response.ok) {
              set({ error: 'account-login-failed', isLoading: false, isAuthenticated: false })
              return false
            }
            const session: { csrf: string; user: { id: string } } = await response.json()
            setTagentCsrf(session.csrf)
            setTagentAccountId(session.user.id)
            set({ isAuthenticated: true, token: null, isLoading: false, lastAuthCheck: Date.now() })
            return true
          }
          const apiUrl = await getApiUrl()

          // Deliberately raw fetch (not apiClient): this probes a candidate
          // password, so the interceptors must not overwrite the Authorization
          // header with the stored token or hard-redirect on 401.
          const response = await fetch(`${apiUrl}/api/notebooks`, {
            method: 'GET',
            headers: {
              'Authorization': `Bearer ${password}`,
              'Content-Type': 'application/json'
            }
          })
          
          if (response.ok) {
            set({ 
              isAuthenticated: true, 
              token: password, 
              isLoading: false,
              lastAuthCheck: Date.now(),
              error: null
            })
            return true
          } else {
            let errorMessage = 'Authentication failed'
            if (response.status === 401) {
              errorMessage = 'Invalid password. Please try again.'
            } else if (response.status === 403) {
              errorMessage = 'Access denied. Please check your credentials.'
            } else if (response.status >= 500) {
              errorMessage = 'Server error. Please try again later.'
            } else {
              errorMessage = `Authentication failed (${response.status})`
            }
            
            set({ 
              error: errorMessage,
              isLoading: false,
              isAuthenticated: false,
              token: null
            })
            return false
          }
        } catch (error) {
          console.error('Network error during auth:', error)
          let errorMessage = 'Authentication failed'
          
          if (error instanceof TypeError && error.message.includes('Failed to fetch')) {
            errorMessage = 'Unable to connect to server. Please check if the API is running.'
          } else if (error instanceof Error) {
            errorMessage = `Network error: ${error.message}`
          } else {
            errorMessage = 'An unexpected error occurred during authentication'
          }
          
          set({ 
            error: errorMessage,
            isLoading: false,
            isAuthenticated: false,
            token: null
          })
          return false
        }
      },
      
      logout: async () => {
        if (get().authMode === 'tagent') {
          const response = await fetch('/agent-api/auth/logout', {
            method: 'POST',
            headers: tagentWriteHeaders(),
          })
          if (!response.ok && response.status !== 401) throw new Error('Unable to sign out')
          clearTagentCsrf()
        }
        set({ 
          isAuthenticated: false, 
          token: null, 
          error: null 
        })
      },
      
      checkAuth: async (force = false) => {
        const state = get()
        const { token, lastAuthCheck, isCheckingAuth, isAuthenticated } = state

        // If already checking, return current auth state
        if (isCheckingAuth) {
          return isAuthenticated
        }

        // If no token, not authenticated
        if (!token && state.authMode !== 'tagent') {
          set({ isAuthenticated: false, authValidated: true })
          return false
        }

        // If we checked recently (within 30 seconds) and are authenticated, skip
        const now = Date.now()
        if (!force && isAuthenticated && lastAuthCheck && (now - lastAuthCheck) < 30000) {
          return true
        }

        set({ isCheckingAuth: true, authValidated: false })

        try {
          if (state.authMode === 'tagent') {
            const response = await fetch('/agent-api/auth/me', { cache: 'no-store' })
            if (!response.ok) {
              clearTagentCsrf()
              set({ isAuthenticated: false, token: null, lastAuthCheck: null, isCheckingAuth: false, authValidated: true })
              return false
            }
            const session: { csrf: string; user: { id: string } } = await response.json()
            if (setTagentAccountId(session.user.id)) {
              window.location.reload()
              return false
            }
            setTagentCsrf(session.csrf)
            set({ isAuthenticated: true, token: null, lastAuthCheck: now, isCheckingAuth: false, authValidated: true })
            return true
          }
          const apiUrl = await getApiUrl()

          // Deliberately raw fetch (not apiClient): a 401 here must update
          // store state, not trigger the interceptor's storage-clear/redirect.
          const response = await fetch(`${apiUrl}/api/notebooks`, {
            method: 'GET',
            headers: {
              'Authorization': `Bearer ${token}`,
              'Content-Type': 'application/json'
            }
          })
          
          if (response.ok) {
            set({ 
              isAuthenticated: true, 
              lastAuthCheck: now,
              isCheckingAuth: false,
              authValidated: true
            })
            return true
          } else {
            set({
              isAuthenticated: false,
              token: null,
              lastAuthCheck: null,
              isCheckingAuth: false,
              authValidated: true
            })
            return false
          }
        } catch (error) {
          console.error('checkAuth error:', error)
          set({ 
            isAuthenticated: false, 
            token: null,
            lastAuthCheck: null,
            isCheckingAuth: false,
            authValidated: true
          })
          return false
        }
      }
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({
        token: state.authMode === 'tagent' ? null : state.token,
        isAuthenticated: state.authMode === 'tagent' ? false : state.isAuthenticated
      }),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true)
      }
    }
  )
)
