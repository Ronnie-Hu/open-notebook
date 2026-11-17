// The TAgent session cookie is HttpOnly. Keep its CSRF value only in memory.
let accountMode = false
let csrf = ''
let accountId = ''

export function setTagentAccountMode(enabled: boolean): void {
  accountMode = enabled
  if (!enabled) {
    csrf = ''
    accountId = ''
  }
}

export function isTagentAccountMode(): boolean {
  return accountMode
}

export function setTagentCsrf(value: string): void {
  csrf = value
}

export function clearTagentCsrf(): void {
  csrf = ''
  accountId = ''
}

export function setTagentAccountId(value: string): boolean {
  const changed = Boolean(accountId && accountId !== value)
  accountId = value
  return changed
}

export function tagentAccountHeaders(): Record<string, string> {
  return accountMode && accountId ? { 'X-Tagent-Account-Id': accountId } : {}
}

export function tagentWriteHeaders(): Record<string, string> {
  return accountMode && csrf ? { 'X-Tagent-CSRF': csrf } : {}
}
