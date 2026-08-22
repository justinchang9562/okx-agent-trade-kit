import type { DashboardData } from './types'

const API = '/api/v1'
let csrfToken = ''

export class ApiError extends Error {
  constructor(public code: string, public status: number) {
    super(code)
  }
}

async function parse<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const code = body?.error?.code ?? `HTTP_${response.status}`
    throw new ApiError(code, response.status)
  }
  return body as T
}

export async function createSession(): Promise<void> {
  const response = await fetch(`${API}/session`, { credentials: 'same-origin' })
  const body = await parse<{ csrf_token: string }>(response)
  csrfToken = body.csrf_token
}

export async function get<T>(path: string): Promise<T> {
  return parse<T>(await fetch(`${API}${path}`, { credentials: 'same-origin' }))
}

export async function write<T>(path: string, body?: unknown, method = 'POST'): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    method,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return parse<T>(response)
}

export async function loadDashboard(): Promise<DashboardData> {
  const paths = [
    '/status', '/health', '/account', '/scanner', '/signals?limit=100',
    '/plans?limit=100', '/orders', '/positions', '/trades', '/logs?limit=200',
    '/audit-log?limit=100', '/settings',
  ]
  const results = await Promise.allSettled(paths.map((path) => get<unknown>(path)))
  const value = (index: number, fallback: unknown) =>
    results[index].status === 'fulfilled' ? results[index].value : fallback
  return {
    status: value(0, undefined) as DashboardData['status'],
    health: value(1, {}) as Record<string, unknown>,
    account: value(2, {}) as Record<string, unknown>,
    scanner: value(3, {}) as Record<string, unknown>,
    signals: value(4, []) as Record<string, unknown>[],
    plans: value(5, []) as DashboardData['plans'],
    orders: value(6, {}) as Record<string, unknown>,
    positions: value(7, {}) as Record<string, unknown>,
    trades: value(8, []) as Record<string, unknown>[],
    logs: value(9, []) as string[],
    audit: value(10, []) as Record<string, unknown>[],
    settings: value(11, {}) as Record<string, unknown>,
  }
}

export function websocketUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}${API}/ws`
}
