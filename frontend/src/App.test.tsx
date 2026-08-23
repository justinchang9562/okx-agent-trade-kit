// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { highRiskWritesAllowed, LiveLockPanel, resolveTheme, SafetyBar } from './App'
import {
  FlattenDialog,
  GenericTable,
  MarketSignals,
  marketStreamState,
  ObjectView,
  OrderLifecycle,
  OverviewMetrics,
  PositionsView,
  SessionControlCard,
  SessionControls,
  SignalHistory,
  SettingsView,
  StopDialog,
} from './dashboardViews'
import { formatPnL, formatTimestamp, formatUSDT } from './formatters'
import { fieldLabel, t, translateCode } from './locales'
import type { ControlState, DashboardData } from './types'

afterEach(cleanup)

const noop = vi.fn()
const control: ControlState = {
  environment: 'DEMO', live_setup_state: 'NOT_CONFIGURED', execution_state: 'DISARMED',
  session_state: 'STOPPED', agent_runtime_state: 'STOPPED', trading_mode: 'STOPPED',
  connection_state: 'CONNECTED', kill_switch_active: false, auto_demo_enabled: false,
  scan_interval_seconds: 15, updated_at_ms: 0,
}

const market = {
  symbols: {
    'BTC-USDT': { symbol: 'BTC-USDT', stream_state: 'CONNECTED', last_price: 76_241, confirmed_candle_timestamps: { '1m': 1_787_467_140_000, '3m': 1_787_467_080_000, '5m': 1_787_466_900_000 } },
    'ETH-USDT': { symbol: 'ETH-USDT', stream_state: 'CONNECTED', last_price: 2_400, confirmed_candle_timestamps: { '1m': 1_787_467_140_000 } },
    'SOL-USDT': { symbol: 'SOL-USDT', stream_state: 'CONNECTED', last_price: 92, confirmed_candle_timestamps: { '1m': 1_787_467_140_000 } },
  },
  metrics: { market_latency_current_ms: 120, confirmed_candle_timestamp: 1_787_467_140_000 },
}

describe('global formatters', () => {
  it('renders zero as a real value rather than unavailable', () => {
    expect(formatUSDT(0, 'zh')).toBe('0.00 USDT')
    expect(formatPnL(0, 'zh')).toBe('0.00 USDT')
  })

  it('renders undefined as unavailable', () => {
    expect(formatUSDT(undefined, 'zh')).toBe('—')
    expect(formatPnL(undefined, 'zh')).toBe('—')
  })

  it('formats epoch timestamps for normal UI', () => {
    const output = formatTimestamp(1_787_467_168_871, 'zh')
    expect(output).not.toContain('1787467168871')
    expect(output).toMatch(/2026/)
  })
})

describe('safe presentation', () => {
  it('never renders object values as [object Object]', () => {
    const html = renderToStaticMarkup(<ObjectView value={{ nested: { status: 'KNOWN' } }} language="zh" empty="无" />)
    expect(html).not.toContain('[object Object]')
    expect(html).toContain('已知')
  })

  it('keeps raw reasons_json out of normal signal columns', () => {
    const html = renderToStaticMarkup(<SignalHistory language="zh" rows={[{ timestamp_ms: 1_787_467_168_871, symbol: 'BTC-USDT', score: 8, signal_strength: 0.8, decision: 'REJECT', reasons: ['SPREAD_TOO_WIDE'], reasons_json: '["SPREAD_TOO_WIDE"]' }]} />)
    expect(html).not.toContain('<th>reasons json</th>')
    expect(html).not.toContain('<th>Reasons json</th>')
    expect(html).toContain('主要原因')
  })

  it('distinguishes HOLD from risk rejection', () => {
    const html = renderToStaticMarkup(<SignalHistory language="zh" rows={[{ timestamp_ms: 1, symbol: 'BTC-USDT', decision: 'HOLD' }, { timestamp_ms: 2, symbol: 'ETH-USDT', decision: 'REJECT' }]} />)
    expect(html).toContain('观望')
    expect(html).toContain('拒绝执行')
  })

  it('translates generic fill headers and side values in Chinese mode', () => {
    const html = renderToStaticMarkup(<GenericTable title="成交记录" language="zh" rows={[{ fill_id: 'f1', order_id: 'o1', symbol: 'BTC-USDT', side: 'sell', size: 0.1, price: 76000 }]} />)
    expect(html).toContain('成交编号')
    expect(html).toContain('成交数量')
    expect(html).toContain('成交价格')
    expect(html).toContain('卖出')
    expect(html).not.toContain('fill id')
  })

  it('translates runtime architecture values in Chinese mode', () => {
    const html = renderToStaticMarkup(<SettingsView language="zh" data={{ runtime_display: { market_source: 'OKX Public WebSocket', strategy_trigger: 'Confirmed 1m Candle', confirmations: ['3m', '5m'] }, runtime: { scan_interval_seconds: 15 } }} />)
    expect(html).toContain('OKX 公共 WebSocket')
    expect(html).toContain('已确认的 1 分钟 K 线')
    expect(html).toContain('3 分钟 / 5 分钟')
    expect(html).not.toContain('Confirmed 1m Candle')
  })
})

describe('session controls', () => {
  const base = { writesReady: true, managedCount: 1, activeEntries: 0, busy: false, onStart: noop, onPause: noop, onStop: noop, onFlatten: noop, language: 'zh' as const }

  it('shows START while stopped', () => {
    const html = renderToStaticMarkup(<SessionControls {...base} state="STOPPED" />)
    expect(html).toContain('启动自动交易')
    expect(html).not.toContain('>暂停<')
  })

  it('shows PAUSE while running', () => {
    const html = renderToStaticMarkup(<SessionControls {...base} state="RUNNING" />)
    expect(html).toContain('>暂停<')
    expect(html).toContain('>停止<')
  })

  it('shows RESUME while paused', () => {
    const html = renderToStaticMarkup(<SessionControls {...base} state="PAUSED" />)
    expect(html).toContain('继续自动交易')
  })

  it('explains that STOP preserves positions and protection', () => {
    const html = renderToStaticMarkup(<StopDialog language="zh" onCancel={noop} onConfirm={noop} />)
    expect(html).toContain('已有托管仓位不会被强制平仓')
    expect(html).toContain('止盈止损保护继续保留')
  })

  it('lists Agent-managed positions in FLATTEN confirmation', () => {
    const html = renderToStaticMarkup(<FlattenDialog language="zh" positions={[{ plan_id: 'p1', symbol: 'BTC-USDT', quantity: 0.012, exit_filled_quantity: 0 }]} externalCount={2} onCancel={noop} onConfirm={noop} />)
    expect(html).toContain('BTC-USDT')
    expect(html).toContain('0.012')
    expect(html).toContain('自动交易代理')
  })

  it('explicitly protects external assets in FLATTEN confirmation', () => {
    const html = renderToStaticMarkup(<FlattenDialog language="zh" positions={[{ symbol: 'BTC-USDT', quantity: 1 }]} externalCount={2} onCancel={noop} onConfirm={noop} />)
    expect(html).toContain('外部钱包资产不会被处理')
    expect(html).toContain('不会自动出售')
  })

  it('keeps all mobile-critical actions in one accessible control group', () => {
    const html = renderToStaticMarkup(<SessionControls {...base} state="RUNNING" />)
    expect(html).toContain('aria-label="自动交易控制"')
    expect(html).toContain('暂停')
    expect(html).toContain('停止')
    expect(html).toContain('全部平仓并停止')
  })
})

describe('risk and lifecycle states', () => {
  it('renders POSITION_UNPROTECTED as critical', () => {
    const html = renderToStaticMarkup(<OrderLifecycle language="zh" value={{ agent_order_lifecycle: [{ plan_id: 'p1', symbol: 'BTC-USDT', state: 'POSITION_UNPROTECTED', protection_state: 'PROTECTION_NOT_FOUND' }] }} />)
    expect(html).toContain('critical-card')
    expect(html).toContain('仓位未受保护')
  })

  it('renders a concrete DEGRADED reason', () => {
    const html = renderToStaticMarkup(<SessionControlCard state="DEGRADED" session={{ degraded_reason: 'ACCOUNT_DATA_UNAVAILABLE' }} market={market} protection={{ protected: 0, total: 0, critical: false }} managedCount={0} activeEntries={0} writesReady={true} busy={false} onStart={noop} onPause={noop} onStop={noop} onFlatten={noop} language="zh" />)
    expect(html).toContain('会话安全降级')
    expect(html).toContain('账户数据不可用')
  })

  it('renders FLATTEN_INCOMPLETE with remaining exposure details', () => {
    const html = renderToStaticMarkup(<SessionControlCard state="FLATTENING" session={{ status: 'FLATTEN_INCOMPLETE', remaining_positions: [{ symbol: 'ETH-USDT', quantity: 0.3 }], unresolved_entries: [{ plan_id: 'p1' }] }} market={market} protection={{ protected: 0, total: 1, critical: true }} managedCount={1} activeEntries={1} writesReady={true} busy={false} onStart={noop} onPause={noop} onStop={noop} onFlatten={noop} language="zh" />)
    expect(html).toContain('平仓尚未完成')
    expect(html).toContain('ETH-USDT')
    expect(html).toContain('继续核对')
  })

  it('renders Live Locked as a normal non-critical state', () => {
    const html = renderToStaticMarkup(<LiveLockPanel language="zh" />)
    expect(html).toContain('data-severity="normal"')
    expect(html).not.toContain('critical-alert')
    expect(html).toContain('实盘交易 · 已锁定 / 尚未实现')
  })
})

describe('market, exposure, and ownership pages', () => {
  it('maps the backend CONNECTED market stream state without requiring AUTO mode', () => {
    expect(marketStreamState(market)).toBe('CONNECTED')
    expect(marketStreamState({ symbols: { 'BTC-USDT': { stream_state: 'STALE' } } })).toBe('STALE')
    expect(marketStreamState({ symbols: {} })).toBe('DISCONNECTED')
  })

  it('shows readable BTC, ETH, and SOL status cards without object coercion', () => {
    const data: DashboardData = { ...({} as DashboardData), signals: [], plans: [], fills: [], trades: [], logs: [], audit: [], market, settings: { symbols: ['BTC-USDT', 'ETH-USDT', 'SOL-USDT'] }, scanner: {} }
    const html = renderToStaticMarkup(<MarketSignals data={data} language="zh" />)
    expect(html).toContain('BTC-USDT')
    expect(html).toContain('ETH-USDT')
    expect(html).toContain('SOL-USDT')
    expect(html).not.toContain('[object Object]')
  })

  it('renders backend-confirmed zero managed exposure, not unavailable', () => {
    const data: DashboardData = { signals: [], plans: [], fills: [], trades: [], logs: [], audit: [], positions: { managed_open_positions: 0, reserved_entry_notional: 0, exposure_summary: { managed_exposure_usdt: 0, managed_exposure_pct: 0 }, managed_positions: [] }, account: { equity_usdt: 1000, available_usdt: 1000 }, settings: { risk: { max_open_positions: 2 } }, market }
    const html = renderToStaticMarkup(<OverviewMetrics data={data} language="zh" />)
    expect(html).toContain('0.00 USDT')
    expect(html).toContain('0.00%')
  })

  it('renders unknown exposure as unavailable', () => {
    const data: DashboardData = { signals: [], plans: [], fills: [], trades: [], logs: [], audit: [], positions: { managed_open_positions: 0, exposure_summary: {}, managed_positions: [] }, account: {}, settings: { risk: { max_open_positions: 2 } } }
    const html = renderToStaticMarkup(<OverviewMetrics data={data} language="zh" />)
    expect(html).toContain('暂无可用数据')
  })

  it('hides presentation dust by default and exposes a show toggle', () => {
    render(<PositionsView language="zh" settings={{ risk: { max_open_positions: 2 } }} value={{ managed_positions: [], managed_open_positions: 0, reserved_entry_notional: 0, exposure_summary: { managed_exposure_usdt: 0, managed_exposure_pct: 0 }, external_wallet_inventory: [{ currency: 'BTC', quantity: 2.35e-9, origin: 'EXTERNAL', managed: false, display_dust: true }] }} />)
    expect(screen.getByText('1 个小额外部资产已隐藏')).toBeTruthy()
    expect(screen.queryByText('BTC')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '显示小额资产' }))
    expect(screen.getByText('BTC')).toBeTruthy()
    expect(screen.getByRole('button', { name: '隐藏小额资产' })).toBeTruthy()
  })

  it('shows Agent and External ownership badges', () => {
    const orderHtml = renderToStaticMarkup(<OrderLifecycle language="zh" value={{ agent_order_lifecycle: [{ plan_id: 'p1', symbol: 'BTC-USDT', state: 'OPEN' }], okx_open_orders: [{ order_id: 'e1', symbol: 'ETH-USDT', state: 'OPEN', origin: 'EXTERNAL' }] }} />)
    expect(orderHtml).toContain('自动交易代理')
    expect(orderHtml).toContain('外部')
  })
})

describe('preserved global controls', () => {
  it('keeps the five independent top status chips', () => {
    const html = renderToStaticMarkup(<SafetyBar control={control} stream="CONNECTED" language="zh" />)
    for (const label of ['环境', '模式', '执行', '紧急停止', '数据流']) expect(html).toContain(label)
    expect(html.match(/status-pill/g)).toHaveLength(5)
  })

  it('fails closed when either stream is stale', () => {
    expect(highRiskWritesAllowed(control, 'CONNECTED')).toBe(true)
    expect(highRiskWritesAllowed(control, 'STALE')).toBe(false)
    expect(highRiskWritesAllowed({ ...control, connection_state: 'STALE' }, 'CONNECTED')).toBe(false)
  })

  it('keeps Chinese/English labels and light/dark/system themes', () => {
    expect(t('zh', 'approval')).toBe('审批中心')
    expect(t('en', 'approval')).toBe('Approval Center')
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })

  it('uses realtime-safe field terminology', () => {
    expect(fieldLabel('zh', 'last_scan_at_ms')).toBe('最近策略评估时间')
    expect(fieldLabel('zh', 'scan_interval_seconds')).toBe('后台健康检查间隔（秒）')
    expect(fieldLabel('zh', 'available_usdt')).toBe('可用 USDT')
    expect(translateCode('zh', 'HOLD')).toBe('观望')
    expect(translateCode('zh', 'REJECT')).toBe('拒绝执行')
    expect(translateCode('zh', 'RealtimeMarketError')).toBe('实时行情错误')
    expect(translateCode('zh', 'REALTIME_MARKET_STALE')).toBe('实时行情新鲜度检查未通过')
  })
})
