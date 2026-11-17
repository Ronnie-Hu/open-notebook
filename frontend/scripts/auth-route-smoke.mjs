import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'

const origin = 'https://tagent.example.org:8502'
const portProbe = createServer()
await new Promise(resolve => portProbe.listen(0, '127.0.0.1', resolve))
const nextPort = portProbe.address().port
await new Promise(resolve => portProbe.close(resolve))
let observed = null
const backend = createServer((request, response) => {
  observed = {
    path: request.url,
    origin: request.headers.origin,
    clientIp: request.headers['x-tagent-client-ip'],
    proxyMark: request.headers['x-tagent-via-frontend'],
  }
  response.setHeader('Content-Type', 'application/json')
  response.setHeader('Set-Cookie', '__Host-tagent_session=probe; Secure; HttpOnly; Path=/')
  response.end('{"ok":true}')
})
await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve))
const backendPort = backend.address().port
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
    OPEN_NOTEBOOK_TRUSTED_PROXY: '1',
    TAGENT_INTERNAL_URL: `http://127.0.0.1:${backendPort}`,
  },
})

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

  const bad = await fetch(`http://127.0.0.1:${nextPort}/agent-api/auth/register`, {
    method: 'POST',
    headers: { Origin: 'https://evil.example.org', 'Content-Type': 'application/json' },
    body: '{}',
  })
  if (bad.status !== 403 || observed) throw new Error('Foreign origin was not blocked')

  const good = await fetch(`http://127.0.0.1:${nextPort}/agent-api/auth/register`, {
    method: 'POST',
    headers: {
      Origin: origin,
      'Content-Type': 'application/json',
      'X-Real-IP': '192.0.2.12',
    },
    body: '{}',
  })
  if (!good.ok ||
      !good.headers.get('set-cookie')?.includes('__Host-tagent_session=probe') ||
      observed?.path !== '/auth/register' ||
      observed?.origin !== origin ||
      observed?.clientIp !== '192.0.2.12' ||
      observed?.proxyMark !== '1') {
    throw new Error(`Unexpected proxy result: ${JSON.stringify({ status: good.status, observed })}`)
  }
  console.log('PASS: real Next route, origin guard, cookie and trusted client IP')
} finally {
  next.kill()
  await new Promise(resolve => backend.close(resolve))
}
