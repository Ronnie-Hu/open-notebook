import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { NextRequest } from 'next/server'

import { GET, POST } from './route'

const context = (action: string) => ({ params: Promise.resolve({ path: [action] }) })
const request = (origin?: string, ip?: string) => ({
  headers: new Headers({
    ...(origin ? { Origin: origin } : {}),
    ...(ip ? { 'X-Real-IP': ip } : {}),
    Cookie: '__Host-tagent_session=test',
  }),
  text: async () => '{"identifier":"student@example.org","password":"test"}',
}) as NextRequest

describe('account auth proxy', () => {
  beforeEach(() => {
    vi.stubEnv('OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE', '1')
    vi.stubEnv('OPEN_NOTEBOOK_PUBLIC_ORIGIN', 'https://tagent.example.org:8502')
    vi.stubEnv('TAGENT_INTERNAL_URL', 'http://127.0.0.1:5001')
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('forwards login only from the notebook origin and preserves the host cookie', async () => {
    const upstreamHeaders = new Headers({ 'Content-Type': 'application/json' })
    upstreamHeaders.append('Set-Cookie', '__Host-tagent_session=opaque; Secure; HttpOnly; Path=/')
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('{"ok":true}', { status: 200, headers: upstreamHeaders })
    )
    vi.stubGlobal('fetch', fetchMock)

    const rejected = await POST(request('https://evil.example.org'), context('login'))
    expect(rejected.status).toBe(403)
    expect(fetchMock).not.toHaveBeenCalled()

    const allowed = await POST(request('https://tagent.example.org:8502'), context('login'))
    expect(allowed.status).toBe(200)
    expect(allowed.headers.get('set-cookie')).toContain('__Host-tagent_session=opaque')
    expect(fetchMock.mock.calls[0][0].toString()).toBe('http://127.0.0.1:5001/auth/login')
    expect(fetchMock.mock.calls[0][1].headers.get('Origin')).toBe('https://tagent.example.org:8502')
  })

  it('uses a trusted ingress IP only when explicitly enabled', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetchMock)
    const browser = request('https://tagent.example.org:8502', '192.0.2.12')
    await POST(browser, context('register'))
    expect(fetchMock.mock.calls[0][1].headers.has('X-Tagent-Client-IP')).toBe(false)

    vi.stubEnv('OPEN_NOTEBOOK_TRUSTED_PROXY', '1')
    await POST(browser, context('register'))
    expect(fetchMock.mock.calls[1][1].headers.get('X-Tagent-Client-IP')).toBe('192.0.2.12')
    expect(fetchMock.mock.calls[1][1].headers.get('X-Tagent-Via-Frontend')).toBe('1')
  })

  it('does not proxy arbitrary TAgent paths', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    expect((await GET(request(), context('admin'))).status).toBe(404)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
