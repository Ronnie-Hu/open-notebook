import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { randomUUID } from 'node:crypto'

const origin = 'https://tagent.example.org:8502'
const fixture = 'http://127.0.0.1:18801'
const marker = await fetch(`${fixture}/__integration_fixture`)
if (!marker.ok || (await marker.json()).fixture !== 'in_memory_tagent_auth') {
  throw new Error('Dedicated TAgent account fixture is required')
}

const portProbe = createServer()
await new Promise(resolve => portProbe.listen(0, '127.0.0.1', resolve))
const nextPort = portProbe.address().port
await new Promise(resolve => portProbe.close(resolve))

const next = spawn(process.execPath, [
  join(process.cwd(), 'node_modules', 'next', 'dist', 'bin', 'next'),
  'start', '-p', String(nextPort), '-H', '127.0.0.1',
], {
  cwd: process.cwd(),
  windowsHide: true,
  stdio: 'ignore',
  env: {
    ...process.env,
    OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE: '1',
    OPEN_NOTEBOOK_PUBLIC_ORIGIN: origin,
    TAGENT_INTERNAL_URL: fixture,
  },
})

const base = `http://127.0.0.1:${nextPort}/agent-api/auth`
try {
  let ready = false
  for (let i = 0; i < 100; i++) {
    if (next.exitCode !== null) throw new Error(`Next exited: ${next.exitCode}`)
    try {
      const response = await fetch(`http://127.0.0.1:${nextPort}/login`)
      if (response.ok) { ready = true; break }
    } catch { /* not listening yet */ }
    await delay(100)
  }
  if (!ready) throw new Error('Next did not become ready')

  const email = `full-notebook-${randomUUID()}@example.org`
  const password = `test-only-${randomUUID()}-password`
  const options = { 'Content-Type': 'application/json', Origin: origin }
  const rejected = await fetch(`${base}/register`, {
    method: 'POST',
    headers: { ...options, Origin: 'https://other.example.org' },
    body: JSON.stringify({ email, password }),
  })
  if (rejected.status !== 403) throw new Error('Foreign origin was accepted')

  const registered = await fetch(`${base}/register`, {
    method: 'POST', headers: options, body: JSON.stringify({ email, password }),
  })
  if (registered.status !== 201) throw new Error(`Registration failed: ${registered.status}`)

  const loggedIn = await fetch(`${base}/login`, {
    method: 'POST', headers: options,
    body: JSON.stringify({ identifier: email, password }),
  })
  if (!loggedIn.ok) throw new Error(`Login failed: ${loggedIn.status}`)
  const cookie = loggedIn.headers.get('set-cookie')?.split(';')[0]
  const loginBody = await loggedIn.json()
  if (!cookie?.startsWith('__Host-tagent_session=') || !loginBody.user?.id || !loginBody.csrf) {
    throw new Error('Next did not forward the Secure session and CSRF response')
  }

  const session = await fetch(`${base}/me`, { headers: { Cookie: cookie } })
  if (!session.ok || (await session.json()).user?.id !== loginBody.user.id) {
    throw new Error('Session did not survive the Next proxy')
  }

  const logout = await fetch(`${base}/logout`, {
    method: 'POST',
    headers: { Origin: origin, Cookie: cookie, 'X-Tagent-CSRF': loginBody.csrf },
  })
  if (!logout.ok) throw new Error(`Logout failed: ${logout.status}`)
  const afterLogout = await fetch(`${base}/me`, { headers: { Cookie: cookie } })
  if (afterLogout.status !== 401) throw new Error('Revoked session remained usable')

  console.log('PASS: built Next route with real TAgent registration, session and logout')
} finally {
  next.kill()
}
