import { afterEach, describe, expect, it, vi } from 'vitest'
import apiClient from '@/lib/api/client'
import { useAuthStore } from './auth-store'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('authentication mode discovery', () => {
  it('keeps login blocked when the status endpoint returns an error', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    vi.spyOn(apiClient, 'get').mockRejectedValue(new Error('temporary outage'))
    useAuthStore.setState({
      authRequired: true,
      authMode: 'password',
      authValidated: true,
      isAuthenticated: true,
    })

    await expect(useAuthStore.getState().checkAuthRequired()).rejects.toThrow('temporary outage')
    const state = useAuthStore.getState()
    expect(state.authRequired).toBeNull()
    expect(state.authMode).toBeNull()
    expect(state.authValidated).toBe(false)
    expect(state.isAuthenticated).toBe(false)
  })
})
