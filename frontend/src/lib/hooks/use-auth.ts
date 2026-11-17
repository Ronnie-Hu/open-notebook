'use client'

import { useAuthStore } from '@/lib/stores/auth-store'
import { useEffect } from 'react'

export function useAuth() {
  const {
    isAuthenticated,
    isLoading,
    login,
    logout,
    checkAuth,
    checkAuthRequired,
    error,
    hasHydrated,
    authRequired,
    isCheckingAuth,
    authValidated,
    authMode
  } = useAuthStore()

  useEffect(() => {
    // Only check auth after the store has hydrated from localStorage
    if (hasHydrated) {
      // First check if auth is required
      if (authRequired === null) {
        checkAuthRequired().then((required) => {
          // If auth is required, check if we have valid credentials
          if (required) {
            checkAuth()
          }
        }).catch(() => undefined)
      } else if (authRequired) {
        // Auth is required, check credentials
        checkAuth()
      }
      // If authRequired === false, we're already authenticated (set in checkAuthRequired)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasHydrated, authRequired])

  useEffect(() => {
    if (authMode !== 'tagent') return
    const recheck = () => {
      if (document.visibilityState === 'visible') void checkAuth(true)
    }
    window.addEventListener('focus', recheck)
    document.addEventListener('visibilitychange', recheck)
    return () => {
      window.removeEventListener('focus', recheck)
      document.removeEventListener('visibilitychange', recheck)
    }
  }, [authMode, checkAuth])

  const handleLogin = async (password: string, email?: string) => {
    const success = await login(password, email)
    if (success) {
      // Check if there's a stored redirect path
      const redirectPath = sessionStorage.getItem('redirectAfterLogin')
      if (redirectPath) {
        sessionStorage.removeItem('redirectAfterLogin')
        if (redirectPath.startsWith('/') && !redirectPath.startsWith('//')) {
          window.location.assign(redirectPath)
        } else {
          window.location.assign('/notebooks')
        }
      } else {
        window.location.assign('/notebooks')
      }
    }
    return success
  }

  const handleLogout = async () => {
    await logout()
    window.location.assign('/login')
  }

  return {
    isAuthenticated,
    isLoading: isLoading || !hasHydrated || authRequired === null || isCheckingAuth || !authValidated,
    error,
    login: handleLogin,
    logout: handleLogout
  }
}
