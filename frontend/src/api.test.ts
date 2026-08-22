import { afterEach, describe, expect, it, vi } from 'vitest'
import { createSession, get, loadDashboard, write } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('local API client', () => {
  it('carries the server CSRF token on writes', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: 'csrf-example' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ execution_state: 'DISARMED' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await createSession()
    await write('/execution/disarm')
    const options = fetchMock.mock.calls[1][1] as RequestInit
    expect((options.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf-example')
    expect(options.credentials).toBe('same-origin')
  })

  it('surfaces stable server error codes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'LIVE_NOT_CONFIGURED' } }), { status: 423 }),
    ))
    await expect(get('/status')).rejects.toEqual(expect.objectContaining({
      code: 'LIVE_NOT_CONFIGURED', status: 423,
    }))
  })

  it('keeps the dashboard usable when one read panel is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if (url.includes('/account')) return Promise.resolve(new Response('{}', { status: 503 }))
      const list = ['/signals', '/plans', '/trades', '/logs', '/audit-log'].some((path) => url.includes(path))
      return Promise.resolve(new Response(JSON.stringify(list ? [] : {}), { status: 200 }))
    }))
    const dashboard = await loadDashboard()
    expect(dashboard.signals).toEqual([])
    expect(dashboard.plans).toEqual([])
    expect(dashboard.account).toEqual({})
  })
})
