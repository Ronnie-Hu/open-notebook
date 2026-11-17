import type { NextRequest } from 'next/server'

const INTERNAL_API_URL = (process.env.INTERNAL_API_URL ||
  (process.env.OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE === '1'
    ? 'http://127.0.0.1:5055' : 'http://localhost:5055')).replace(/\/$/, '')

function safeAccountTarget(): boolean {
  try {
    const url = new URL(INTERNAL_API_URL)
    return url.protocol === 'http:' &&
      (url.hostname === '127.0.0.1' || url.hostname === '[::1]') &&
      !url.username && !url.password && url.pathname === '/' && !url.search && !url.hash
  } catch {
    return false
  }
}

export async function sseProxy(req: NextRequest, upstreamPath: string) {
  if (process.env.OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE === '1' && !safeAccountTarget()) {
    return new Response(null, { status: 503 })
  }
  const body = await req.text()
  const auth = req.headers.get('authorization')
  const cookie = req.headers.get('cookie')
  const csrf = req.headers.get('x-tagent-csrf')
  const accountId = req.headers.get('x-tagent-account-id')
  const origin = req.headers.get('origin')

  const upstream = await fetch(`${INTERNAL_API_URL}${upstreamPath}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(auth ? { Authorization: auth } : {}),
      ...(cookie ? { Cookie: cookie } : {}),
      ...(csrf ? { 'X-Tagent-CSRF': csrf } : {}),
      ...(accountId ? { 'X-Tagent-Account-Id': accountId } : {}),
      ...(origin ? { Origin: origin } : {}),
      Accept: 'text/event-stream',
    },
    body,
    cache: 'no-store',
  })

  if (!upstream.ok || !upstream.body) {
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { 'Content-Type': upstream.headers.get('content-type') ?? 'application/json' },
    })
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  })
}
