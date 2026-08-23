import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError, createSession, loadDashboard, websocketUrl, write } from './api'
import { fieldLabel, translateCode, type Language } from './locales'
import type { Connection, ControlState, DashboardData, StreamEvent } from './types'

type Section = 'overview' | 'market' | 'positions' | 'trades' | 'performance' | 'advanced'
export type ThemePreference = 'system' | 'light' | 'dark'

const emptyData: DashboardData = {
  signals: [], plans: [], fills: [], trades: [], logs: [], audit: [],
}

export function highRiskWritesAllowed(control: ControlState | undefined, stream: Connection): boolean {
  return stream === 'CONNECTED' && control?.connection_state === 'CONNECTED' && !control.kill_switch_active
}

export function flattenControlVisible(sessionState: string, managedCount: number): boolean {
  return sessionState !== 'STOPPED' || managedCount > 0
}

export function resolveTheme(preference: ThemePreference, darkScheme: boolean): 'light' | 'dark' {
  return preference === 'system' ? (darkScheme ? 'dark' : 'light') : preference
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function numeric(value: unknown): number | undefined {
  const parsed = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : Number.NaN
  return Number.isFinite(parsed) ? parsed : undefined
}

function format(value: unknown, language: Language, digits = 2): string {
  const parsed = numeric(value)
  return parsed === undefined ? '—' : parsed.toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function errorCode(error: unknown): string {
  if (error instanceof ApiError) return error.code
  return error instanceof Error ? error.message : 'UNKNOWN_ERROR'
}

function copy(language: Language, zh: string, en: string): string {
  return language === 'zh' ? zh : en
}

function StatusPill({ label, value, tone = 'muted' }: { label: string; value: string; tone?: string }) {
  return <span className={`status-pill ${tone}`}><i /><span><small>{label}</small><b>{value}</b></span></span>
}

export function SafetyBar({ control, stream, language = 'en', market = 'DISCONNECTED' }: {
  control?: ControlState; stream: Connection; language?: Language; market?: string
}) {
  const session = control?.session_state ?? 'STOPPED'
  return <header className="topbar" aria-label={language === 'zh' ? '交易安全状态' : 'Safety status'}>
    <div className="brand-lockup"><span className="brand-mark">OKX</span><span><b>{copy(language, '自动交易 Agent', 'Automatic Trading Agent')}</b><small>{copy(language, '本机 · 模拟盘', 'Local · Demo only')}</small></span></div>
    <div className="safety-states">
      <StatusPill label={copy(language, '环境', 'Environment')} value={translateCode(language, control?.environment ?? 'DEMO')} tone="blue" />
      <StatusPill label="OKX" value={translateCode(language, control?.connection_state ?? 'DISCONNECTED')} tone={control?.connection_state === 'CONNECTED' ? 'green' : 'danger'} />
      <StatusPill label={copy(language, '行情', 'Market')} value={translateCode(language, market)} tone={market === 'REALTIME' ? 'green' : 'danger'} />
      <StatusPill label={copy(language, '会话', 'Session')} value={translateCode(language, session)} tone={session === 'RUNNING' ? 'green' : session === 'DEGRADED' ? 'danger' : 'muted'} />
      <StatusPill label={copy(language, '控制流', 'Control')} value={translateCode(language, stream)} tone={stream === 'CONNECTED' ? 'green' : 'danger'} />
    </div>
  </header>
}

function Card({ label, value, detail, tone = '' }: { label: string; value: string; detail: string; tone?: string }) {
  return <article className={`metric-card ${tone}`}><small>{label}</small><strong>{value}</strong><span>{detail}</span></article>
}

function Panel({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return <section className="panel"><header className="panel-header"><h2>{title}</h2>{aside}</header>{children}</section>
}

function ObjectView({ value, language }: { value: unknown; language: Language }) {
  if (value === undefined || value === null) return <p className="empty">—</p>
  if (Array.isArray(value)) {
    if (!value.length) return <p className="empty">—</p>
    return <div className="object-list">{value.map((item, index) => <ObjectView key={index} value={item} language={language} />)}</div>
  }
  const record = asRecord(value)
  if (!Object.keys(record).length) return <span>{translateCode(language, value)}</span>
  return <dl className="object-grid">{Object.entries(record).map(([key, item]) => <div key={key}><dt>{fieldLabel(language, key)}</dt><dd>{item && typeof item === 'object' ? <ObjectView value={item} language={language} /> : translateCode(language, item)}</dd></div>)}</dl>
}

function App() {
  const [data, setData] = useState<DashboardData>(emptyData)
  const [stream, setStream] = useState<Connection>('DISCONNECTED')
  const [section, setSection] = useState<Section>('overview')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [language, setLanguage] = useState<Language>('zh')
  const [theme, setTheme] = useState<ThemePreference>('system')
  const reconnect = useRef<number | undefined>(undefined)

  const refresh = useCallback(async () => setData(await loadDashboard()), [])
  const control = data.status?.control
  const sessionState = control?.session_state ?? 'STOPPED'
  const health = asRecord(data.health)
  const eligibility = asRecord(health.trading_eligibility)
  const positions = asRecord(data.positions)
  const account = asRecord(data.account)
  const marketPayload = asRecord(data.market ?? data.status?.market)
  const marketSymbols = asRecord(marketPayload.symbols)
  const realtime = Object.keys(marketSymbols).length > 0 && Object.values(marketSymbols).every((item) => asRecord(item).stream_state === 'CONNECTED')
  const marketState = realtime ? 'REALTIME' : 'DISCONNECTED'
  const managedCount = numeric(positions.managed_open_positions) ?? numeric(data.status?.core.managed_open_positions) ?? 0
  const pnl = account.daily_pnl ?? data.status?.observability?.daily_pnl ?? data.status?.core.daily_pnl
  const writesReady = highRiskWritesAllowed(control, stream)

  useEffect(() => {
    const media = window.matchMedia?.('(prefers-color-scheme: dark)')
    const apply = () => {
      const resolved = resolveTheme(theme, media?.matches ?? false)
      document.documentElement.dataset.theme = resolved
      document.documentElement.style.colorScheme = resolved
    }
    apply()
    media?.addEventListener?.('change', apply)
    return () => media?.removeEventListener?.('change', apply)
  }, [theme])

  useEffect(() => {
    let active = true
    let socket: WebSocket | null = null
    const connect = () => {
      if (!active) return
      socket = new WebSocket(websocketUrl())
      socket.onopen = () => setStream('CONNECTED')
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as StreamEvent
          if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'heartbeat.ack', sequence: event.sequence }))
          setStream('CONNECTED')
          if (event.type === 'snapshot') setData((current) => ({ ...current, ...(event.data as unknown as DashboardData) }))
          else if (event.type === 'control.state') setData((current) => current.status ? ({ ...current, status: { ...current.status, control: event.data as unknown as ControlState } }) : current)
          else if (event.type === 'account.updated') setData((current) => ({ ...current, account: event.data }))
          else if (event.type === 'orders.updated') setData((current) => ({ ...current, orders: event.data }))
          else if (event.type === 'positions.updated') setData((current) => ({ ...current, positions: event.data }))
          else if (event.type === 'fills.updated') setData((current) => ({ ...current, fills: (event.data.fills ?? []) as Record<string, unknown>[] }))
          else if (event.type !== 'heartbeat') void refresh().catch(() => undefined)
        } catch { setStream('STALE') }
      }
      socket.onclose = () => {
        setStream('DISCONNECTED')
        if (active) reconnect.current = window.setTimeout(() => void createSession().then(connect), 1500)
      }
      socket.onerror = () => setStream('DISCONNECTED')
    }
    void createSession().then(() => { if (active) { connect(); void refresh() } }).catch((error) => setNotice(errorCode(error)))
    return () => { active = false; window.clearTimeout(reconnect.current); socket?.close() }
  }, [refresh])

  const act = async (name: string, path: string, confirmation?: string) => {
    if (confirmation && !window.confirm(confirmation)) return
    setBusy(name); setNotice('')
    try { await write(path); await refresh() } catch (error) { setNotice(errorCode(error)) } finally { setBusy('') }
  }

  const controls = <div className="session-actions">
    {(sessionState === 'STOPPED' || sessionState === 'PAUSED' || sessionState === 'DEGRADED') && <button className="button start-button" disabled={!writesReady || busy !== ''} onClick={() => void act('start', '/session/start', copy(language, '启动自动模拟盘交易会话？', 'Start automatic Demo trading?'))}>{sessionState === 'PAUSED' ? copy(language, '继续自动交易', 'Resume automatic trading') : copy(language, '启动自动交易', 'Start automatic trading')}</button>}
    {sessionState === 'RUNNING' && <button className="button secondary" disabled={busy !== ''} onClick={() => void act('pause', '/session/pause')}>{copy(language, '暂停', 'Pause')}</button>}
    {sessionState !== 'STOPPED' && <button className="button secondary" disabled={busy !== ''} onClick={() => void act('stop', '/session/stop')}>{copy(language, '停止', 'Stop')}</button>}
    {flattenControlVisible(sessionState, managedCount) && <button className="button danger" disabled={!writesReady || busy !== ''} onClick={() => void act('flatten', '/session/flatten', copy(language, '仅平掉 Agent 管理的全部仓位并停止？', 'Flatten every Agent-managed position and stop?'))}>{copy(language, '全部平仓并停止', 'Flatten all & stop')}</button>}
  </div>

  const tabs: { id: Section; label: string }[] = [
    { id: 'overview', label: copy(language, '总览', 'Overview') },
    { id: 'market', label: copy(language, '市场与信号', 'Market & signals') },
    { id: 'positions', label: copy(language, '仓位', 'Positions') },
    { id: 'trades', label: copy(language, '交易', 'Trades') },
    { id: 'performance', label: copy(language, '表现', 'Performance') },
    { id: 'advanced', label: copy(language, '高级', 'Advanced') },
  ]

  return <div className="app-shell">
    <SafetyBar control={control} stream={stream} language={language} market={marketState} />
    <nav className="simple-nav">{tabs.map((tab) => <button className={section === tab.id ? 'active' : ''} key={tab.id} onClick={() => setSection(tab.id)}>{tab.label}</button>)}<span className="nav-spacer" /><button onClick={() => setLanguage(language === 'zh' ? 'en' : 'zh')}>{language === 'zh' ? 'EN' : '中文'}</button><button onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '☀' : '☾'}</button></nav>
    <main className="main-content">
      {notice && <div className="notice error">{notice}</div>}
      {section === 'overview' && <>
        <div className="hero-copy"><span>OKX DEMO · SPOT</span><h1>{copy(language, '你说 START，Agent 负责交易。', 'You say START. The Agent trades.')}</h1><p>{copy(language, '确定性策略、Risk Manager、仓位计算与 OrderManager 始终位于执行热路径；AI 只负责解释与审计。', 'Deterministic strategy, Risk Manager, sizing and OrderManager stay in the execution hot path. AI explains and audits.')}</p></div>
        <div className="metric-grid">
          <Card label={copy(language, 'OKX 连接', 'OKX connection')} value={translateCode(language, control?.connection_state ?? 'DISCONNECTED')} detail="Agent Trade Kit MCP" tone={control?.connection_state === 'CONNECTED' ? 'good' : 'bad'} />
          <Card label={copy(language, '市场数据', 'Market data')} value={translateCode(language, marketState)} detail="OKX Public WebSocket" tone={realtime ? 'good' : 'bad'} />
          <Card label={copy(language, '自动会话', 'Auto session')} value={translateCode(language, sessionState)} detail={copy(language, '重启后始终停止', 'Always stopped after restart')} tone={sessionState === 'RUNNING' ? 'good' : ''} />
          <Card label={copy(language, '风险状态', 'Risk status')} value={translateCode(language, eligibility.eligible ? 'PASS' : eligibility.reason ?? 'NOT_READY')} detail={copy(language, 'RiskManager 最终否决权', 'RiskManager final veto')} tone={eligibility.eligible ? 'good' : 'bad'} />
          <Card label={copy(language, '管理仓位', 'Managed positions')} value={String(managedCount)} detail={copy(language, '不自动接管外部资产', 'External assets are never adopted')} />
          <Card label={copy(language, '当日 PnL', 'Daily PnL')} value={`${format(pnl, language)} USDT`} detail={copy(language, '已实现损益', 'Realized performance')} tone={(numeric(pnl) ?? 0) >= 0 ? 'good' : 'bad'} />
        </div>
        <Panel title={copy(language, '自动交易会话', 'Automatic trading session')} aside={<span className={`session-badge ${sessionState.toLowerCase()}`}>{translateCode(language, sessionState)}</span>}><p className="session-explainer">{sessionState === 'RUNNING' ? copy(language, '已授权自动开仓；每笔交易仍须通过完整策略、风控、仓位与实时复核。', 'Automatic entries are authorized; every trade still passes strategy, risk, sizing and realtime revalidation.') : copy(language, '当前不会建立新仓位；仓位保护与核对仍会继续。', 'No new position can be opened. Protection and reconciliation continue.')}</p>{controls}</Panel>
      </>}
      {section === 'market' && <><Panel title={copy(language, '实时市场', 'Realtime market')}><ObjectView value={data.market} language={language} /></Panel><Panel title={copy(language, '信号', 'Signals')}><ObjectView value={data.signals} language={language} /></Panel></>}
      {section === 'positions' && <Panel title={copy(language, 'Agent 与外部仓位', 'Agent and external positions')}><ObjectView value={data.positions} language={language} /></Panel>}
      {section === 'trades' && <><Panel title={copy(language, '订单', 'Orders')}><ObjectView value={data.orders} language={language} /></Panel><Panel title={copy(language, '成交', 'Fills')}><ObjectView value={data.fills} language={language} /></Panel><Panel title={copy(language, '交易记录', 'Trades')}><ObjectView value={data.trades} language={language} /></Panel></>}
      {section === 'performance' && <Panel title={copy(language, '表现', 'Performance')}><ObjectView value={{ daily_pnl: pnl, managed_positions: managedCount, trades: data.trades.length }} language={language} /></Panel>}
      {section === 'advanced' && <><Panel title={copy(language, '高级 / 研究 / 开发', 'Advanced / Research / Developer')}><p className="session-explainer">{copy(language, '回测、Walk Forward、人工诊断审批、运行内部状态与原始 JSON 保留在这里，不进入主要交易流程。', 'Backtest, walk-forward, manual diagnostic approval, runtime internals and raw JSON remain here, outside the primary trading flow.')}</p><ObjectView value={data.settings} language={language} /></Panel><Panel title={copy(language, '审计', 'Audit')}><ObjectView value={data.audit} language={language} /></Panel><Panel title={copy(language, '脱敏日志', 'Redacted logs')}><pre className="log-view">{data.logs.join('\n') || '—'}</pre></Panel></>}
    </main>
  </div>
}

export default App
