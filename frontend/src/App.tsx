import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ApiError, createSession, loadDashboard, websocketUrl, write } from './api'
import { fieldLabel, t, translateCode, type Language, type TextKey } from './locales'
import type { Connection, ControlState, DashboardData, StreamEvent } from './types'

type Section = 'overview' | 'scanner' | 'approval' | 'orders' | 'positions' | 'trades' | 'backtest' | 'logs' | 'settings'
export type ThemePreference = 'system' | 'light' | 'dark'
type IconName = Section | 'shield' | 'language' | 'appearance' | 'close' | 'lock'

const navigation: { id: Section; label: TextKey; subtitle: TextKey }[] = [
  { id: 'overview', label: 'overview', subtitle: 'overviewSubtitle' },
  { id: 'scanner', label: 'scanner', subtitle: 'scannerSubtitle' },
  { id: 'approval', label: 'approval', subtitle: 'approvalSubtitle' },
  { id: 'orders', label: 'orders', subtitle: 'ordersSubtitle' },
  { id: 'positions', label: 'positions', subtitle: 'positionsSubtitle' },
  { id: 'trades', label: 'trades', subtitle: 'tradesSubtitle' },
  { id: 'backtest', label: 'backtest', subtitle: 'backtestSubtitle' },
  { id: 'logs', label: 'logs', subtitle: 'logsSubtitle' },
  { id: 'settings', label: 'settings', subtitle: 'settingsSubtitle' },
]

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

function savedPreference<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = window.localStorage.getItem(key) as T | null
    return value && allowed.includes(value) ? value : fallback
  } catch {
    return fallback
  }
}

function persistPreference(key: string, value: string) {
  try { window.localStorage.setItem(key, value) } catch { /* browser storage can be disabled */ }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function numeric(value: unknown): number | undefined {
  const parsed = typeof value === 'number' ? value : typeof value === 'string' && value !== '' ? Number(value) : Number.NaN
  return Number.isFinite(parsed) ? parsed : undefined
}

function format(value: unknown, language: Language, digits = 2): string {
  const parsed = numeric(value)
  return parsed === undefined ? '—' : parsed.toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function stamp(value: unknown, language: Language): string {
  const parsed = numeric(value)
  return parsed ? new Date(parsed).toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US') : '—'
}

function errorCode(error: unknown): string {
  if (error instanceof ApiError) return error.code
  return error instanceof Error ? error.message : 'UNKNOWN_ERROR'
}

function copy(language: Language, zh: string, en: string): string {
  return language === 'zh' ? zh : en
}

function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></>,
    scanner: <><path d="M4 19V9"/><path d="M10 19V5"/><path d="M16 19v-7"/><path d="M22 19H2"/></>,
    approval: <><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></>,
    orders: <><path d="M6 2h9l5 5v15H6z"/><path d="M14 2v6h6"/><path d="M9 13h8M9 17h8"/></>,
    positions: <><circle cx="12" cy="12" r="9"/><path d="M8 12h8M12 8v8"/></>,
    trades: <><path d="M4 17l5-5 4 3 7-8"/><path d="M15 7h5v5"/></>,
    backtest: <><path d="M4 4v16h16"/><path d="M7 15l4-4 3 2 5-7"/></>,
    logs: <><path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21H9.6v-.09A1.7 1.7 0 0 0 8.5 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.51-1H3V10h.09A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.51V3h4v.09A1.7 1.7 0 0 0 15 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9a1.7 1.7 0 0 0 1.51 1H21v4h-.09A1.7 1.7 0 0 0 19.4 15z"/></>,
    shield: <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></>,
    language: <><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18"/></>,
    appearance: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42"/></>,
    close: <path d="M6 6l12 12M18 6L6 18"/>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></>,
  }
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function StatusPill({ label, value, tone = 'muted', critical = false }: { label: string; value: string; tone?: string; critical?: boolean }) {
  return <span className={`status-pill ${tone} ${critical ? 'critical' : ''}`}><i /><span><small>{label}</small><b>{value}</b></span></span>
}

export function SafetyBar({ control, stream, language = 'en' }: { control?: ControlState; stream: Connection; language?: Language }) {
  const environment = control?.environment ?? 'DEMO'
  return <header className="topbar" aria-label={language === 'zh' ? '交易安全状态' : 'Safety status'}>
    <div className="brand-lockup"><span className="brand-mark">OKX</span><span><b>{t(language, 'product')}</b><small>{t(language, 'localControl')}</small></span></div>
    <div className="safety-states">
      <StatusPill label={t(language, 'environment')} value={translateCode(language, environment)} tone={environment === 'DEMO' ? 'blue' : 'danger'} />
      <StatusPill label={t(language, 'mode')} value={translateCode(language, control?.trading_mode ?? 'STOPPED')} />
      <StatusPill label={t(language, 'execution')} value={translateCode(language, control?.execution_state ?? 'DISARMED')} tone={control?.execution_state === 'ARMED' ? 'amber' : 'muted'} critical />
      <StatusPill label={t(language, 'killSwitch')} value={translateCode(language, control?.kill_switch_active ? 'ACTIVE' : 'OFF')} tone={control?.kill_switch_active ? 'danger' : 'muted'} critical />
      <StatusPill label={t(language, 'stream')} value={translateCode(language, stream)} tone={stream === 'CONNECTED' ? 'green' : stream === 'STALE' ? 'amber' : 'danger'} />
    </div>
  </header>
}

function PreferenceControls({ language, theme, setLanguage, setTheme }: { language: Language; theme: ThemePreference; setLanguage: (value: Language) => void; setTheme: (value: ThemePreference) => void }) {
  return <div className="preference-controls compact">
    <div className="preference-group" aria-label={t(language, 'language')}><Icon name="language" size={16} />{(['zh', 'en'] as Language[]).map((value) => <button key={value} className={language === value ? 'selected' : ''} onClick={() => setLanguage(value)}>{value === 'zh' ? '中文' : 'EN'}</button>)}</div>
    <div className="preference-group theme-group" aria-label={t(language, 'theme')}><Icon name="appearance" size={16} />{(['system', 'light', 'dark'] as ThemePreference[]).map((value) => <button key={value} className={theme === value ? 'selected' : ''} onClick={() => setTheme(value)}>{t(language, value)}</button>)}</div>
  </div>
}

function Panel({ title, eyebrow, children, actions, className = '' }: { title: string; eyebrow?: string; children: ReactNode; actions?: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><header className="panel-header"><div>{eyebrow && <small>{eyebrow}</small>}<h2>{title}</h2></div>{actions}</header>{children}</section>
}

function ObjectView({ value, language, empty }: { value: unknown; language: Language; empty: string }) {
  if (value === undefined || value === null) return <p className="empty">{empty}</p>
  if (Array.isArray(value)) {
    if (!value.length) return <p className="empty">{empty}</p>
    return <div className="object-list">{value.map((item, index) => <ObjectView key={index} value={item} language={language} empty={empty} />)}</div>
  }
  const record = asRecord(value)
  if (!Object.keys(record).length) return <span>{translateCode(language, value)}</span>
  return <dl className="object-grid">{Object.entries(record).map(([key, item]) => <div key={key}><dt>{fieldLabel(language, key)}</dt><dd>{item && typeof item === 'object' ? <ObjectView value={item} language={language} empty={empty} /> : translateCode(language, item)}</dd></div>)}</dl>
}

function Metric({ label, value, detail, accent }: { label: string; value: unknown; detail?: string; accent?: 'blue' | 'amber' }) {
  return <article className={`metric ${accent ?? ''}`}><small>{label}</small><strong>{String(value ?? '—')}</strong>{detail && <span>{detail}</span>}</article>
}

function DataTable({ title, rows, language }: { title: string; rows: Record<string, unknown>[]; language: Language }) {
  const keys = useMemo(() => Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 8), [rows])
  return <Panel title={title} eyebrow={`${rows.length} ${t(language, 'records')}`}>{rows.length ? <div className="table-wrap"><table><thead><tr>{keys.map((key) => <th key={key}>{fieldLabel(language, key)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id ?? row.plan_id ?? index)}>{keys.map((key) => <td key={key}>{row[key] && typeof row[key] === 'object' ? JSON.stringify(row[key]) : translateCode(language, row[key])}</td>)}</tr>)}</tbody></table></div> : <p className="empty">{t(language, 'noRecords')}</p>}</Panel>
}

function App() {
  const [data, setData] = useState<DashboardData>(emptyData)
  const [stream, setStream] = useState<Connection>('DISCONNECTED')
  const [section, setSection] = useState<Section>('overview')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [language, setLanguageState] = useState<Language>(() => savedPreference('okx-language', ['zh', 'en'], 'zh'))
  const [theme, setThemeState] = useState<ThemePreference>(() => savedPreference('okx-dashboard-theme-v2', ['system', 'light', 'dark'], 'dark'))
  const reconnect = useRef<number | undefined>(undefined)

  const setLanguage = (value: Language) => { setLanguageState(value); persistPreference('okx-language', value) }
  const setTheme = (value: ThemePreference) => { setThemeState(value); persistPreference('okx-dashboard-theme-v2', value) }
  const refresh = useCallback(async () => setData(await loadDashboard()), [])
  const control = data.status?.control
  const sessionState = control?.session_state ?? 'STOPPED'
  const health = asRecord(data.health)
  const eligibility = asRecord(health.trading_eligibility)
  const positions = asRecord(data.positions)
  const account = asRecord(data.account)
  const exposure = asRecord(account.exposure ?? positions.account_exposure)
  const observability = asRecord(data.status?.observability)
  const risk = asRecord(asRecord(data.settings).risk)
  const managedCount = numeric(positions.managed_open_positions) ?? numeric(data.status?.core.managed_open_positions) ?? 0
  const equity = numeric(account.equity_usdt)
  const walletExposureUsdt = numeric(exposure.wallet_exposure_usdt)
  const walletExposurePct = walletExposureUsdt !== undefined && equity !== undefined && equity > 0
    ? walletExposureUsdt / equity
    : numeric(observability.wallet_exposure_pct)
  const managedExposureUsdt = numeric(exposure.managed_exposure_usdt)
    ?? numeric(observability.managed_exposure_usdt)
  const pending = data.plans.filter((plan) => plan.ui_status === 'PENDING_APPROVAL')
  const writesReady = highRiskWritesAllowed(control, stream)
  const currentPage = navigation.find((item) => item.id === section) ?? navigation[0]

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
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en'
  }, [language])

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
    try { await write(path); await refresh() } catch (error) { setNotice(translateCode(language, errorCode(error))) } finally { setBusy('') }
  }

  const controls = <div className="session-actions">
    {(sessionState === 'STOPPED' || sessionState === 'PAUSED' || sessionState === 'DEGRADED') && <button className="button start-button" disabled={!writesReady || busy !== ''} onClick={() => void act('start', '/session/start', copy(language, '启动自动模拟盘交易会话？', 'Start automatic Demo trading?'))}>{sessionState === 'PAUSED' ? copy(language, '继续自动交易', 'Resume automatic trading') : copy(language, '启动自动交易', 'Start automatic trading')}</button>}
    {sessionState === 'RUNNING' && <button className="button secondary" disabled={busy !== ''} onClick={() => void act('pause', '/session/pause')}>{copy(language, '暂停自动交易', 'Pause automatic trading')}</button>}
    {sessionState !== 'STOPPED' && <button className="button secondary" disabled={busy !== ''} onClick={() => void act('stop', '/session/stop')}>{copy(language, '停止自动交易', 'Stop automatic trading')}</button>}
    {flattenControlVisible(sessionState, managedCount) && <button className="button danger" disabled={!writesReady || busy !== ''} onClick={() => void act('flatten', '/session/flatten', copy(language, '仅平掉 Agent 管理的全部仓位并停止？', 'Flatten every Agent-managed position and stop?'))}>{copy(language, '全部平仓并停止', 'Flatten all & stop')}</button>}
  </div>

  return <div className="app-shell">
    <SafetyBar control={control} stream={stream} language={language} />
    <aside className="sidebar">
      <nav aria-label={copy(language, '主导航', 'Primary navigation')}>{navigation.map((item) => <button key={item.id} className={section === item.id ? 'active' : ''} onClick={() => setSection(item.id)}><Icon name={item.id} /><span>{t(language, item.label)}</span>{item.id === 'approval' && pending.length > 0 && <b className="nav-badge">{pending.length}</b>}</button>)}</nav>
      <div className="live-lock"><Icon name="lock" /><div><b>{t(language, 'liveLocked')}</b><p>{t(language, 'liveDescription')}</p></div></div>
    </aside>
    <main className="main-content">
      <div className="page-heading"><div><span className="page-kicker">OKX {translateCode(language, 'DEMO')} <i /> {t(language, 'localOnly')}</span><h1>{t(language, currentPage.label)}</h1><p>{t(language, currentPage.subtitle)}</p></div><PreferenceControls language={language} theme={theme} setLanguage={setLanguage} setTheme={setTheme} /></div>
      {notice && <div className="notice error" role="status"><span>{notice}</span><button aria-label={t(language, 'dismiss')} onClick={() => setNotice('')}><Icon name="close" size={16} /></button></div>}
      {stream !== 'CONNECTED' && <div className="stale-banner"><Icon name="shield" /><div><b>{t(language, 'writeLock')}</b><span>{t(language, 'writeLockDetail')}</span></div></div>}
      <div className="page-content" key={`${section}-${language}`}>
        {section === 'overview' && <>
          <div className="metric-row">
            <Metric label={t(language, 'equity')} value={`$${format(account.equity_usdt, language)}`} detail={t(language, 'demoValuation')} accent="blue" />
            <Metric label={t(language, 'available')} value={`$${format(account.available_usdt, language)}`} detail={t(language, 'usdtAvailable')} />
            <Metric label={t(language, 'managedSlots')} value={`${format(positions.position_slots_in_use, language, 0)} / ${format(risk.max_open_positions, language, 0)}`} detail={t(language, 'walletExcluded')} />
            <Metric label={t(language, 'pendingPlans')} value={String(pending.length)} detail={t(language, 'ttlBound')} accent={pending.length ? 'amber' : undefined} />
          </div>
          <div className="metric-row compact">
            <Metric label={fieldLabel(language, 'connection_freshness_age_seconds')} value={numeric(observability.connection_freshness_age_seconds) === undefined ? (stream === 'CONNECTED' ? copy(language, '已实时连接', 'Connected live') : t(language, 'dataUnavailable')) : `${format(observability.connection_freshness_age_seconds, language, 1)}s`} />
            <Metric label={fieldLabel(language, 'last_scan_at_ms')} value={numeric(observability.last_scan_at_ms) === undefined ? copy(language, '尚未扫描', 'Not scanned yet') : stamp(observability.last_scan_at_ms, language)} />
            <Metric label={fieldLabel(language, 'last_health_at_ms')} value={numeric(observability.last_health_at_ms) === undefined ? copy(language, '尚未检查', 'Not checked yet') : stamp(observability.last_health_at_ms, language)} />
            <Metric label={fieldLabel(language, 'wallet_exposure_pct')} value={walletExposurePct === undefined ? t(language, 'dataUnavailable') : `${format(walletExposurePct * 100, language, 1)}%`} />
            <Metric label={fieldLabel(language, 'managed_exposure_usdt')} value={managedExposureUsdt === undefined ? t(language, 'dataUnavailable') : `$${format(managedExposureUsdt, language)}`} />
            <Metric label={fieldLabel(language, 'daily_pnl')} value={`$${format(observability.daily_pnl, language)}`} />
            <Metric label={fieldLabel(language, 'consecutive_losses')} value={format(observability.consecutive_losses, language, 0)} />
          </div>
          <div className="two-column">
            <Panel title={t(language, 'tradingEligibility')} eyebrow={eligibility.eligible ? t(language, 'ready') : t(language, 'blocked')}><div className={`eligibility ${eligibility.eligible ? 'pass' : 'blocked'}`}><span>{eligibility.eligible ? '✓' : '!'}</span><div><b>{translateCode(language, eligibility.reason ?? 'DATA_UNAVAILABLE')}</b><p>{Array.isArray(eligibility.blocking_reasons) ? eligibility.blocking_reasons.map((item) => translateCode(language, item)).join(' · ') : '—'}</p></div></div></Panel>
            <Panel title={t(language, 'runtimePosture')} eyebrow={t(language, 'independentSafety')}><dl className="definition-grid"><dt>{t(language, 'agent')}</dt><dd>{translateCode(language, control?.agent_runtime_state ?? 'STOPPED')}</dd><dt>{t(language, 'mode')}</dt><dd>{translateCode(language, control?.trading_mode ?? 'STOPPED')}</dd><dt>{t(language, 'execution')}</dt><dd>{translateCode(language, control?.execution_state ?? 'DISARMED')}</dd><dt>{t(language, 'killSwitch')}</dt><dd>{translateCode(language, control?.kill_switch_active ? 'ACTIVE' : 'OFF')}</dd></dl></Panel>
          </div>
          <Panel title={copy(language, '自动交易会话', 'Automatic trading session')} eyebrow={translateCode(language, sessionState)}><p className="panel-copy">{sessionState === 'RUNNING' ? copy(language, '自动会话运行中；每笔交易仍须通过策略、风控、仓位计算与实时复核。', 'The automatic session is running; every trade still passes strategy, risk, sizing, and realtime revalidation.') : copy(language, '当前不会建立新仓位；账户核对与已有仓位保护仍继续运行。', 'No new positions will be opened. Account reconciliation and existing protection continue.')}</p>{controls}</Panel>
          <Panel title={t(language, 'accountExposure')} eyebrow={t(language, 'exposureSubtitle')}><ObjectView value={account.exposure ?? positions.account_exposure} language={language} empty={t(language, 'dataUnavailable')} /></Panel>
        </>}
        {section === 'scanner' && <><Panel title={t(language, 'configuredScanner')} eyebrow={t(language, 'strategyOutput')}><ObjectView value={data.scanner} language={language} empty={t(language, 'scanEmpty')} /></Panel><DataTable title={t(language, 'signalHistory')} rows={data.signals} language={language} /></>}
        {section === 'approval' && <DataTable title={t(language, 'serverPlans')} rows={data.plans.map((plan) => ({ ...plan }))} language={language} />}
        {section === 'orders' && <><Panel title={t(language, 'persistentLifecycle')} eyebrow={t(language, 'coreStates')}><ObjectView value={data.orders} language={language} empty={t(language, 'noOrders')} /></Panel><DataTable title={copy(language, '成交记录', 'Fills')} rows={data.fills} language={language} /></>}
        {section === 'positions' && <Panel title={t(language, 'walletManaged')} eyebrow={t(language, 'exposureSemantics')}><ObjectView value={data.positions} language={language} empty={t(language, 'dataUnavailable')} /></Panel>}
        {section === 'trades' && <DataTable title={t(language, 'recordedTrades')} rows={data.trades} language={language} />}
        {section === 'backtest' && <Panel title={t(language, 'historicalEvaluation')} eyebrow={t(language, 'noTuning')}><p className="panel-copy">{copy(language, '回测与滚动前向验证属于研究工具，不进入实时交易热路径。运行参数与生产风控规则保持分离。', 'Backtests and walk-forward evaluation are research tools outside the live trading hot path. Their parameters remain separate from production risk rules.')}</p><ObjectView value={asRecord(data.settings).scalping} language={language} empty={t(language, 'evaluationEmpty')} /></Panel>}
        {section === 'logs' && <div className="two-column"><Panel title={t(language, 'runtimeLog')} eyebrow={`${data.logs.length} ${t(language, 'lines')}`}><pre className="log-view">{data.logs.join('\n') || '—'}</pre></Panel><Panel title={t(language, 'transitionAudit')} eyebrow={`${data.audit.length} ${t(language, 'events')}`}><ObjectView value={data.audit} language={language} empty={t(language, 'noAudit')} /></Panel></div>}
        {section === 'settings' && <><Panel title={t(language, 'appearance')} eyebrow={t(language, 'appearanceSubtitle')}><PreferenceControls language={language} theme={theme} setLanguage={setLanguage} setTheme={setTheme} /></Panel><Panel title={t(language, 'runtimeSettings')} eyebrow={t(language, 'allowlistOnly')}><ObjectView value={data.settings} language={language} empty={t(language, 'dataUnavailable')} /></Panel></>}
      </div>
    </main>
  </div>
}

export default App
