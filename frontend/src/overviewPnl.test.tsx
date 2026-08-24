import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { managedUnrealizedPnL, OverviewMetrics } from './dashboardViews'
import type { DashboardData } from './types'

function dashboardData(managed: Record<string, unknown>[], lastPrice: number): DashboardData {
  return {
    signals: [], plans: [], fills: [], trades: [], logs: [], audit: [],
    positions: {
      managed_open_positions: managed.length,
      reserved_entry_notional: 0,
      exposure_summary: {},
      managed_positions: managed,
    },
    account: { equity_usdt: 1000, available_usdt: 800 },
    settings: { risk: { max_open_positions: 2 } },
    market: {
      symbols: {
        'BTC-USDT': { stream_state: 'CONNECTED', last_price: lastPrice },
      },
    },
  }
}

describe('overview unrealized PnL', () => {
  it('shows a profitable managed position in green', () => {
    const managed = [{
      symbol: 'BTC-USDT', entry_price: 100, quantity: 2, exit_filled_quantity: 0,
    }]
    expect(managedUnrealizedPnL(managed, dashboardData(managed, 110).market)).toBe(20)

    const html = renderToStaticMarkup(
      <OverviewMetrics data={dashboardData(managed, 110)} language="zh" />,
    )
    expect(html).toContain('未实现损益')
    expect(html).toContain('class="positive"')
    expect(html).toContain('20.00 USDT')
  })

  it('shows a losing managed position in red', () => {
    const managed = [{
      symbol: 'BTC-USDT', entry_price: 100, quantity: 2, exit_filled_quantity: 0,
    }]
    const html = renderToStaticMarkup(
      <OverviewMetrics data={dashboardData(managed, 90)} language="zh" />,
    )
    expect(html).toContain('class="negative"')
    expect(html).toContain('-20.00 USDT')
  })

  it('does not invent PnL when the realtime stream is stale', () => {
    expect(managedUnrealizedPnL(
      [{ symbol: 'BTC-USDT', entry_price: 100, quantity: 2 }],
      { symbols: { 'BTC-USDT': { stream_state: 'STALE', last_price: 110 } } },
    )).toBeUndefined()
  })
})
