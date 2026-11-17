import { afterEach, describe, expect, it } from 'vitest'
import {
  clearTagentCsrf,
  setTagentAccountId,
  setTagentAccountMode,
  setTagentCsrf,
  tagentAccountHeaders,
  tagentWriteHeaders,
} from './tagent-session'

afterEach(() => {
  setTagentAccountMode(false)
  clearTagentCsrf()
})

describe('TAgent browser session', () => {
  it('sends the account claim and CSRF only in account mode', () => {
    setTagentAccountMode(true)
    setTagentAccountId('account-a')
    setTagentCsrf('session-secret')
    expect(tagentAccountHeaders()).toEqual({ 'X-Tagent-Account-Id': 'account-a' })
    expect(tagentWriteHeaders()).toEqual({ 'X-Tagent-CSRF': 'session-secret' })
    setTagentAccountMode(false)
    expect(tagentAccountHeaders()).toEqual({})
    expect(tagentWriteHeaders()).toEqual({})
  })

  it('detects a browser session switched to a different account', () => {
    setTagentAccountMode(true)
    expect(setTagentAccountId('account-a')).toBe(false)
    expect(setTagentAccountId('account-a')).toBe(false)
    expect(setTagentAccountId('account-b')).toBe(true)
  })
})
