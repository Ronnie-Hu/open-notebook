import { afterEach, expect, it, vi } from 'vitest'
import type { NextRequest } from 'next/server'
import { sseProxy } from './_sse-proxy'

const originalFetch = global.fetch

afterEach(() => {
  global.fetch = originalFetch
})

it('forwards the verified browser session and CSRF to a streaming API call', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response('data: ready\n\n', {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
  )
  global.fetch = fetchMock
  const request = new Request('https://tagent.example.org/api/search/ask', {
    method: 'POST',
    headers: {
      Cookie: '__Host-tagent_session=opaque',
      Origin: 'https://tagent.example.org',
      'X-Tagent-CSRF': 'csrf-token',
      'X-Tagent-Account-Id': 'account-a',
    },
    body: '{}',
  }) as NextRequest

  const response = await sseProxy(request, '/api/search/ask')
  expect(response.status).toBe(200)
  expect(await response.text()).toBe('data: ready\n\n')
  const forwarded = fetchMock.mock.calls[0][1] as RequestInit
  expect(forwarded.headers).toMatchObject({
    Cookie: '__Host-tagent_session=opaque',
    Origin: 'https://tagent.example.org',
    'X-Tagent-CSRF': 'csrf-token',
    'X-Tagent-Account-Id': 'account-a',
  })
})
