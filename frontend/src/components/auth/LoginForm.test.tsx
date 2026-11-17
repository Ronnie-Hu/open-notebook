import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { LoginForm } from './LoginForm'
import { useAuth } from '@/lib/hooks/use-auth'
import { useAuthStore } from '@/lib/stores/auth-store'

vi.mock('@/lib/config', () => ({
  getConfig: vi.fn().mockResolvedValue({
    apiUrl: '', version: 'test', buildTime: '2026-11-17T00:00:00Z',
  }),
  getApiUrl: vi.fn().mockResolvedValue(''),
}))

describe('full notebook TAgent account form', () => {
  const login = vi.fn().mockResolvedValue(true)

  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({
      hasHydrated: true,
      authRequired: true,
      authMode: 'tagent',
      isAuthenticated: false,
      authValidated: true,
    })
    vi.mocked(useAuth).mockReturnValue({
      isAuthenticated: false,
      isLoading: false,
      error: null,
      login,
      logout: vi.fn().mockResolvedValue(undefined),
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('offers self-registration and sends email/password to the account route', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)
    render(<LoginForm />)
    fireEvent.click(await screen.findByRole('button', { name: 'auth.createAccount' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'auth.emailPlaceholder' }), {
      target: { value: 'student@example.org' },
    })
    fireEvent.change(screen.getByPlaceholderText('auth.passwordPlaceholder'), {
      target: { value: 'a long test password' },
    })
    fireEvent.submit(screen.getByRole('button', { name: 'auth.backToLogin' }).closest('form')!)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/agent-api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: 'student@example.org', password: 'a long test password' }),
      })
    })
    expect(await screen.findByText('auth.registrationSuccess')).toBeInTheDocument()
    expect(login).not.toHaveBeenCalled()
  })

  it('passes the email and password to the account login action', async () => {
    render(<LoginForm />)
    fireEvent.change(await screen.findByRole('textbox', { name: 'auth.emailPlaceholder' }), {
      target: { value: 'student@example.org' },
    })
    fireEvent.change(screen.getByPlaceholderText('auth.passwordPlaceholder'), {
      target: { value: 'a long test password' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'auth.signIn' }))

    await waitFor(() => {
      expect(login).toHaveBeenCalledWith('a long test password', 'student@example.org')
    })
  })
})
