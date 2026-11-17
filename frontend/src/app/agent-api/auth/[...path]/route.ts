import { isIP } from 'node:net'
import type { NextRequest } from 'next/server'

export const runtime = 'nodejs'

const GET_PATHS = new Set(['me', 'status'])
const POST_PATHS = new Set(['login', 'register', 'logout'])

function loopbackTarget(): URL | null {
  try {
    const url = new URL(process.env.TAGENT_INTERNAL_URL || 'http://127.0.0.1:5001')
    if (url.protocol !== 'http:' ||
        !['127.0.0.1', '[::1]'].includes(url.hostname) ||
        url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
      return null
    }
    return url
  } catch {
    return null
  }
}

async function proxyAuth(request: NextRequest, method: 'GET' | 'POST', path: string[]) {
  if (process.env.OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE !== '1') {
    return new Response(null, { status: 404 })
  }
  const action = path.join('/')
  if (!(method === 'GET' ? GET_PATHS : POST_PATHS).has(action)) {
    return new Response(null, { status: 404 })
  }
  const target = loopbackTarget()
  if (!target) return new Response(null, { status: 503 })

  const publicOrigin = (process.env.OPEN_NOTEBOOK_PUBLIC_ORIGIN || '').replace(/\/$/, '')
  if (method === 'POST' &&
      (!publicOrigin.startsWith('https://') ||
       request.headers.get('origin') !== publicOrigin)) {
    return new Response(null, { status: 403 })
  }

  const headers = new Headers()
  for (const name of ['cookie', 'x-tagent-csrf', 'x-tagent-account-id']) {
    const value = request.headers.get(name)
    if (value) headers.set(name, value)
  }
  if (method === 'POST') {
    headers.set('Origin', publicOrigin)
    headers.set('Content-Type', 'application/json')
  }
  // Only a trusted ingress may supply this header. It must strip any value
  // sent by the browser and prevent direct access to the Next.js port.
  if (process.env.OPEN_NOTEBOOK_TRUSTED_PROXY === '1') {
    const clientIp = request.headers.get('x-real-ip') || ''
    if (isIP(clientIp)) {
      headers.set('X-Tagent-Via-Frontend', '1')
      headers.set('X-Tagent-Client-IP', clientIp)
    }
  }

  const upstream = await fetch(new URL(`/auth/${action}`, target), {
    method,
    headers,
    body: method === 'POST' ? await request.text() : undefined,
    cache: 'no-store',
    redirect: 'manual',
  })
  const responseHeaders = new Headers({
    'Cache-Control': 'no-store',
    'Content-Type': upstream.headers.get('content-type') || 'application/json',
  })
  for (const cookie of upstream.headers.getSetCookie()) {
    responseHeaders.append('Set-Cookie', cookie)
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  })
}

type AuthRoute = { params: Promise<{ path: string[] }> }

export async function GET(request: NextRequest, context: AuthRoute) {
  return proxyAuth(request, 'GET', (await context.params).path)
}

export async function POST(request: NextRequest, context: AuthRoute) {
  return proxyAuth(request, 'POST', (await context.params).path)
}
