import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ApiError, createSession, get, loadDashboard, websocketUrl, write } from './api'
import { fieldLabel, t, translateCode, type Language, type TextKey } from './locales'
import type { Connection, ControlState, DashboardData, Plan, StreamEvent, TradingMode } from './types'

type Section = 'overview' | 'scanner' | 'approval' | 'orders' | 'positions' | 'trades' | 'backtest' | 'logs' | 'settings'
export type ThemePreference = 'system' | 'light' | 'dark'
type IconName = Section | 'shield' | 'language' | 'appearance' | 'close' | 'lock' | 'refresh'

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

const emptyData: DashboardData = { signals: [], plans: [], trades: [], logs: [], audit: [] }

export function highRiskWritesAllowed(control: ControlState | undefined, stream: Connection): boolean {
  return stream === 'CONNECTED' && control?.connection_state === 'CONNECTED' && !control.kill_switch_active
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
  try { window.localStorage.setItem(key, value) } catch { /* browser storage may be disabled */ }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function number(value: unknown): number | undefined {
  const parsed = typeof value === 'number' ? value : typeof value === 'string' && value !== '' ? Number(value) : undefined
  return parsed !== undefined && Number.isFinite(parsed) ? parsed : undefined
}

function fmt(value: unknown, digits = 2, language: Language = 'en'): string {
  const parsed = number(value)
  return parsed === undefined ? '—' : parsed.toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US', { maximumFractionDigits: digits })
}

function stamp(value: unknown, language: Language): string {
  const parsed = number(value)
  return parsed ? new Date(parsed).toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US') : '—'
}

function reason(error: unknown): string {
  if (error instanceof ApiError) return error.code
  return error instanceof Error ? error.message : 'UNKNOWN_ERROR'
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
    refresh: <><path d="M20 6v5h-5"/><path d="M4 18v-5h5"/><path d="M18.5 9A7 7 0 0 0 6 6.5L4 11M5.5 15A7 7 0 0 0 18 17.5l2-4.5"/></>,
  }
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
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

function StatusPill({ label, value, tone = 'muted', critical = false }: { label: string; value: string; tone?: string; critical?: boolean }) {
  return <span className={`status-pill ${tone} ${critical ? 'critical' : ''}`}><i /><span><small>{label}</small><b>{value}</b></span></span>
}

function Panel({ title, eyebrow, children, actions, className = '' }: { title: string; eyebrow?: string; children: ReactNode; actions?: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><header className="panel-header"><div>{eyebrow && <small>{eyebrow}</small>}<h2>{title}</h2></div>{actions}</header>{children}</section>
}

function Empty({ text }: { text: string }) {
  return <div className="empty"><span className="empty-orb">—</span><p>{text}</p></div>
}

function Button({ children, danger, secondary, disabled, onClick, title, icon }: { children: ReactNode; danger?: boolean; secondary?: boolean; disabled?: boolean; onClick: () => void; title?: string; icon?: IconName }) {
  return <button className={`button ${danger ? 'danger' : ''} ${secondary ? 'secondary' : ''}`} disabled={disabled} onClick={onClick} title={title}>{icon && <Icon name={icon} size={15} />}{children}</button>
}

function PreferenceControls({ language, theme, setLanguage, setTheme, compact = false }: { language: Language; theme: ThemePreference; setLanguage: (value: Language) => void; setTheme: (value: ThemePreference) => void; compact?: boolean }) {
  return <div className={`preference-controls ${compact ? 'compact' : ''}`}>
    <div className="preference-group" aria-label={t(language, 'language')}><Icon name="language" size={16} />{(['zh', 'en'] as Language[]).map((value) => <button key={value} className={language === value ? 'selected' : ''} onClick={() => setLanguage(value)}>{value === 'zh' ? '中文' : 'EN'}</button>)}</div>
    <div className="preference-group theme-group" aria-label={t(language, 'theme')}><Icon name="appearance" size={16} />{(['system', 'light', 'dark'] as ThemePreference[]).map((value) => <button key={value} className={theme === value ? 'selected' : ''} onClick={() => setTheme(value)}>{t(language, value)}</button>)}</div>
  </div>
}

function ObjectView({ value, empty, language }: { value: unknown; empty: string; language: Language }) {
  if (value === undefined || value === null || (Array.isArray(value) && value.length === 0) || (typeof value === 'object' && !Array.isArray(value) && Object.keys(value as object).length === 0)) return <Empty text={empty} />
  if (Array.isArray(value)) return <div className="object-list">{value.map((item, index) => <ObjectCard key={index} value={item} language={language} />)}</div>
  return <ObjectCard value={value} language={language} />
}

function ObjectCard({ value, language }: { value: unknown; language: Language }) {
  const record = asRecord(value)
  if (!Object.keys(record).length) return <span className="primitive-value">{translateCode(language, value)}</span>
  return <dl className="object-grid">{Object.entries(record).map(([key, item]) => <div key={key}><dt>{fieldLabel(language, key)}</dt><dd><DisplayValue value={item} language={language} /></dd></div>)}</dl>
}

function DisplayValue({ value, language }: { value: unknown; language: Language }) {
  if (Array.isArray(value)) {
    if (!value.length) return <span>—</span>
    return <span className="value-list">{value.map((item, index) => <span key={index}>{typeof item === 'object' && item !== null ? <DisplayValue value={item} language={language} /> : translateCode(language, item)}</span>)}</span>
  }
  if (value && typeof value === 'object') {
    return <span className="nested-record">{Object.entries(value as Record<string, unknown>).map(([key, item]) => <span key={key}><small>{fieldLabel(language, key)}</small><DisplayValue value={item} language={language} /></span>)}</span>
  }
  return <>{translateCode(language, value)}</>
}

function App() {
  const [section, setSection] = useState<Section>('overview')
  const [language, setLanguageState] = useState<Language>(() => savedPreference('okx-language', ['zh', 'en'], 'zh'))
  const [theme, setThemeState] = useState<ThemePreference>(() => savedPreference('okx-theme', ['system', 'light', 'dark'], 'system'))
  const [data, setData] = useState<DashboardData>(emptyData)
  const [stream, setStream] = useState<Connection>('DISCONNECTED')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)
  const [backtest, setBacktest] = useState<Record<string, unknown> | null>(null)
  const [backtestSymbol, setBacktestSymbol] = useState('BTC-USDT')
  const [backtestDays, setBacktestDays] = useState(7)
  const lastMessage = useRef(0), reconnectTimer = useRef<number | undefined>(undefined), refreshTimer = useRef<number | undefined>(undefined), languageRef = useRef(language)

  const setLanguage = (value: Language) => { setLanguageState(value); persistPreference('okx-language', value) }
  const setTheme = (value: ThemePreference) => { setThemeState(value); persistPreference('okx-theme', value) }

  useEffect(() => {
    const media = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null
    const apply = () => { const resolved = resolveTheme(theme, media?.matches ?? false); document.documentElement.dataset.theme = resolved; document.documentElement.dataset.themePreference = theme; document.documentElement.style.colorScheme = resolved }
    apply(); media?.addEventListener?.('change', apply)
    return () => media?.removeEventListener?.('change', apply)
  }, [theme])
  useEffect(() => { languageRef.current = language; document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en' }, [language])

  const refresh = useCallback(async () => setData(await loadDashboard()), [])

  useEffect(() => {
    let active = true, socket: WebSocket | null = null
    const scheduleRefresh = () => { window.clearTimeout(refreshTimer.current); refreshTimer.current = window.setTimeout(() => void refresh().catch(() => undefined), 300) }
    const connect = () => {
      if (!active) return
      socket = new WebSocket(websocketUrl())
      socket.onopen = () => { lastMessage.current = Date.now(); setStream('CONNECTED') }
      socket.onmessage = (message) => {
        lastMessage.current = Date.now(); setStream('CONNECTED')
        try {
          const event = JSON.parse(message.data) as StreamEvent
          if (socket?.readyState === WebSocket.OPEN && Number.isInteger(event.sequence) && event.sequence >= 0) {
            socket.send(JSON.stringify({ type: 'heartbeat.ack', sequence: event.sequence }))
          }
          if (event.type === 'snapshot') setData((current) => ({ ...current, ...(event.data as unknown as DashboardData) }))
          else if (event.type === 'control.state') setData((current) => current.status ? ({ ...current, status: { ...current.status, control: event.data as unknown as ControlState } }) : current)
          else if (event.type === 'health.updated') setData((current) => ({ ...current, health: event.data }))
          else if (event.type === 'account.updated') setData((current) => ({ ...current, account: event.data }))
          else if (event.type === 'scanner.updated') setData((current) => ({ ...current, scanner: event.data }))
          else if (event.type !== 'heartbeat') scheduleRefresh()
        } catch { setStream('STALE') }
      }
      socket.onerror = () => setStream('DISCONNECTED')
      socket.onclose = () => { setStream('DISCONNECTED'); if (active) reconnectTimer.current = window.setTimeout(() => void createSession().then(connect).catch(() => { if (active) reconnectTimer.current = window.setTimeout(connect, 2000) }), 2000) }
    }
    void (async () => {
      try {
        await createSession(); if (active) connect()
        const incremental: [string, keyof DashboardData, unknown][] = [['/status', 'status', undefined], ['/health', 'health', {}], ['/account', 'account', {}], ['/scanner', 'scanner', {}], ['/signals?limit=100', 'signals', []], ['/plans?limit=100', 'plans', []], ['/orders', 'orders', {}], ['/positions', 'positions', {}], ['/trades', 'trades', []], ['/logs?limit=200', 'logs', []], ['/audit-log?limit=100', 'audit', []], ['/settings', 'settings', {}]]
        for (const [path, key, fallback] of incremental) void get<unknown>(path).then((value) => setData((current) => ({ ...current, [key]: value }))).catch(() => setData((current) => ({ ...current, [key]: fallback })))
      } catch (error) { setNotice({ tone: 'error', text: `${t(languageRef.current, 'startupFailed')}: ${reason(error)}` }) }
    })()
    const staleClock = window.setInterval(() => { if (lastMessage.current && Date.now() - lastMessage.current > 7000) setStream('STALE') }, 1000)
    return () => { active = false; window.clearInterval(staleClock); window.clearTimeout(reconnectTimer.current); window.clearTimeout(refreshTimer.current); socket?.close() }
  }, [refresh])

  const control = data.status?.control
  const highRiskReady = highRiskWritesAllowed(control, stream)
  const executionReady = highRiskReady && control?.execution_state === 'ARMED' && control?.agent_runtime_state === 'RUNNING'
  const pending = useMemo(() => data.plans.filter((plan) => plan.ui_status === 'PENDING_APPROVAL'), [data.plans])
  const currentPage = navigation.find((item) => item.id === section) ?? navigation[0]
  const act = async (name: string, callback: () => Promise<unknown>) => { setBusy(name); setNotice(null); try { await callback(); await refresh(); setNotice({ tone: 'ok', text: `${name} ${t(language, 'completed')}` }) } catch (error) { setNotice({ tone: 'error', text: `${name}: ${translateCode(language, reason(error))}` }) } finally { setBusy('') } }
  const setMode = (mode: TradingMode) => void act(`${t(language, 'modeAction')} ${mode}`, () => write('/mode', { mode }))
  const revalidate = (plan: Plan) => void act(t(language, 'planRevalidation'), async () => { setPreview(await write<Record<string, unknown>>(`/plans/${encodeURIComponent(plan.plan_id)}/preview`)) })
  const approve = () => void act(t(language, 'demoApproval'), async () => { await write(`/plans/${encodeURIComponent(String(preview?.plan_id ?? ''))}/approve`, { approval_challenge: preview?.approval_challenge }); setPreview(null) })

  return <div className="app-shell">
    <SafetyBar control={control} stream={stream} language={language} />
    <aside className="sidebar"><nav aria-label={language === 'zh' ? '主导航' : 'Primary navigation'}>{navigation.map((item) => <button key={item.id} className={section === item.id ? 'active' : ''} onClick={() => setSection(item.id)}><Icon name={item.id} /><span>{t(language, item.label)}</span>{item.id === 'approval' && pending.length > 0 && <b className="nav-badge">{pending.length}</b>}</button>)}</nav><div className="live-lock"><Icon name="lock" /><div><b>{t(language, 'liveLocked')}</b><p>{t(language, 'liveDescription')}</p></div></div></aside>
    <main><div className="page-heading"><div><span className="page-kicker">OKX {translateCode(language, 'DEMO')} <i /> {t(language, 'localOnly')}</span><h1>{t(language, currentPage.label)}</h1><p>{t(language, currentPage.subtitle)}</p></div><PreferenceControls language={language} theme={theme} setLanguage={setLanguage} setTheme={setTheme} compact /></div>
      {notice && <div className={`notice ${notice.tone}`} role="status"><span>{notice.text}</span><button aria-label={t(language, 'dismiss')} onClick={() => setNotice(null)}><Icon name="close" size={16} /></button></div>}
      {stream !== 'CONNECTED' && <div className="stale-banner"><Icon name="shield" /><div><b>{t(language, 'writeLock')}</b><span>{t(language, 'writeLockDetail')}</span></div></div>}
      <div className="page-content" key={`${section}-${language}`}>
        {section === 'overview' && <Overview data={data} control={control} pending={pending.length} language={language} />}
        {section === 'scanner' && <Scanner data={data} language={language} busy={busy} run={() => void act(t(language, 'marketScan'), () => write('/scanner/run'))} />}
        {section === 'approval' && <Approval plans={data.plans} language={language} executionReady={Boolean(executionReady)} busy={busy} revalidate={revalidate} reject={(plan) => void act(t(language, 'planRejection'), () => write(`/plans/${encodeURIComponent(plan.plan_id)}/reject`))} />}
        {section === 'orders' && <OrdersPanel value={data.orders} language={language} />}
        {section === 'positions' && <PositionsPanel value={data.positions} language={language} />}
        {section === 'trades' && <DataTable title={t(language, 'recordedTrades')} rows={data.trades} language={language} />}
        {section === 'backtest' && <BacktestPanel result={backtest} language={language} symbol={backtestSymbol} days={backtestDays} busy={busy} setSymbol={setBacktestSymbol} setDays={setBacktestDays} run={(walk) => void act(walk ? t(language, 'walkForwardAction') : t(language, 'backtestAction'), async () => setBacktest(await write('/backtest', { symbol: backtestSymbol, days: backtestDays, walk_forward: walk }) as Record<string, unknown>))} />}
        {section === 'logs' && <LogsPanel logs={data.logs} audit={data.audit} language={language} />}
        {section === 'settings' && <SettingsPanel data={data} control={control} stream={stream} language={language} theme={theme} setLanguage={setLanguage} setTheme={setTheme} highRiskReady={Boolean(highRiskReady)} busy={busy} setMode={setMode} act={act} />}
      </div>
    </main>
    {preview && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={t(language, 'approveDemo')} onMouseDown={(event) => { if (event.target === event.currentTarget) setPreview(null) }}><div className="modal"><header><div><small>{t(language, 'freshPreview')}</small><h2>{t(language, 'approveDemo')}</h2></div><button className="close-button" aria-label={t(language, 'close')} onClick={() => setPreview(null)}><Icon name="close" /></button></header><div className="preview-grid"><Metric label={t(language, 'symbol')} value={preview.symbol} /><Metric label={t(language, 'executablePrice')} value={fmt(preview.current_executable_price, 8, language)} /><Metric label={t(language, 'positionSize')} value={fmt(preview.position_size, 8, language)} /><Metric label={t(language, 'riskAmount')} value={`$${fmt(preview.final_risk_amount, 2, language)}`} /><Metric label={t(language, 'stopPrice')} value={fmt(preview.final_stop, 8, language)} /><Metric label={t(language, 'takeProfit')} value={fmt(preview.final_take_profit, 8, language)} /><Metric label={fieldLabel(language, 'final_risk_reward')} value={fmt(preview.final_risk_reward, 2, language)} /><Metric label={fieldLabel(language, 'estimated_usdt')} value={`$${fmt(preview.estimated_usdt, 2, language)}`} /><Metric label={fieldLabel(language, 'entry_deviation_pct')} value={`${fmt(preview.entry_deviation_pct, 3, language)}%`} /><Metric label={fieldLabel(language, 'spread_pct')} value={`${fmt(preview.spread_pct, 3, language)}%`} /></div><ChallengeCountdown expiresAt={Number(preview.challenge_expires_at_ms ?? 0)} language={language} /><div className="modal-actions"><Button secondary onClick={() => setPreview(null)}>{t(language, 'cancel')}</Button><Button disabled={!preview.approval_challenge || !executionReady || Boolean(busy) || Number(preview.challenge_expires_at_ms ?? 0) <= Date.now()} onClick={approve}>{t(language, 'submitDemo')}</Button></div></div></div>}
  </div>
}

function Overview({ data, control, pending, language }: { data: DashboardData; control?: ControlState; pending: number; language: Language }) {
  const account = asRecord(data.account), positions = asRecord(data.positions), eligibility = asRecord(asRecord(data.health).trading_eligibility), risk = asRecord(asRecord(data.settings).risk), observability = asRecord(data.status?.observability)
  const expiry = number(observability.pending_plan_nearest_expiry_ms), freshness = number(observability.connection_freshness_age_seconds)
  return <><div className="metric-row"><Metric label={t(language, 'equity')} value={`$${fmt(account.equity_usdt, 2, language)}`} detail={t(language, 'demoValuation')} accent="blue" /><Metric label={t(language, 'available')} value={`$${fmt(account.available_usdt, 2, language)}`} detail={t(language, 'usdtAvailable')} /><Metric label={t(language, 'managedSlots')} value={`${fmt(positions.position_slots_in_use, 0, language)} / ${fmt(risk.max_open_positions, 0, language)}`} detail={t(language, 'walletExcluded')} /><Metric label={t(language, 'pendingPlans')} value={String(pending)} detail={expiry ? `${Math.max(0, Math.floor((expiry - Date.now()) / 1000))}s` : t(language, 'ttlBound')} accent={pending ? 'amber' : undefined} /></div><div className="metric-row compact"><Metric label={fieldLabel(language, 'connection_freshness_age_seconds')} value={freshness === undefined ? '—' : `${fmt(freshness, 1, language)}s`} /><Metric label={fieldLabel(language, 'last_scan_at_ms')} value={observability.last_scan_at_ms ? stamp(observability.last_scan_at_ms, language) : '—'} /><Metric label={fieldLabel(language, 'last_health_at_ms')} value={observability.last_health_at_ms ? stamp(observability.last_health_at_ms, language) : '—'} /><Metric label={fieldLabel(language, 'wallet_exposure_pct')} value={number(observability.wallet_exposure_pct) === undefined ? '—' : `${fmt(Number(observability.wallet_exposure_pct) * 100, 1, language)}%`} /><Metric label={fieldLabel(language, 'managed_exposure_usdt')} value={`$${fmt(observability.managed_exposure_usdt, 2, language)}`} /><Metric label={fieldLabel(language, 'daily_pnl')} value={`$${fmt(observability.daily_pnl, 2, language)}`} /><Metric label={fieldLabel(language, 'consecutive_losses')} value={fmt(observability.consecutive_losses, 0, language)} /></div><div className="two-column"><Panel title={t(language, 'tradingEligibility')} eyebrow={eligibility.eligible ? t(language, 'ready') : t(language, 'blocked')}><div className={`eligibility ${eligibility.eligible ? 'pass' : 'blocked'}`}><span>{eligibility.eligible ? '✓' : '!'}</span><div><b title={String(eligibility.reason ?? 'DATA_UNAVAILABLE')}>{translateCode(language, eligibility.reason ?? 'DATA_UNAVAILABLE')}</b><p>{Array.isArray(eligibility.blocking_reasons) ? eligibility.blocking_reasons.map((item) => translateCode(language, item)).join(' · ') : '—'}</p></div></div></Panel><Panel title={t(language, 'runtimePosture')} eyebrow={t(language, 'independentSafety')}><dl className="definition-grid"><dt>{t(language, 'agent')}</dt><dd>{translateCode(language, control?.agent_runtime_state ?? 'STOPPED')}</dd><dt>{t(language, 'mode')}</dt><dd>{translateCode(language, control?.trading_mode ?? 'STOPPED')}</dd><dt>{t(language, 'execution')}</dt><dd>{translateCode(language, control?.execution_state ?? 'DISARMED')}</dd><dt>{t(language, 'killSwitch')}</dt><dd>{translateCode(language, control?.kill_switch_active ? 'ACTIVE' : 'OFF')}</dd></dl></Panel></div><Panel title={t(language, 'accountExposure')} eyebrow={t(language, 'exposureSubtitle')}><ObjectView value={account.exposure} empty={t(language, 'dataUnavailable')} language={language} /></Panel></>
}

function Metric({ label, value, detail, accent }: { label: string; value: unknown; detail?: string; accent?: 'blue' | 'amber' }) { return <div className={`metric ${accent ?? ''}`}><small>{label}</small><strong>{String(value ?? '—')}</strong>{detail && <span>{detail}</span>}</div> }

function Scanner({ data, busy, run, language }: { data: DashboardData; busy: string; run: () => void; language: Language }) {
  const scans = Object.entries(data.scanner ?? {})
  return <><Panel title={t(language, 'configuredScanner')} eyebrow={t(language, 'strategyOutput')} actions={<Button icon="refresh" onClick={run} disabled={Boolean(busy)}>{t(language, 'runScan')}</Button>}>{scans.length ? <div className="scan-grid">{scans.map(([symbol, raw]) => { const item = asRecord(raw), strength = number(item.signal_strength); return <article key={symbol} className="scan-card"><header><b>{symbol}</b><span className={`decision ${String(item.decision).toLowerCase()}`}>{translateCode(language, item.decision ?? item.status ?? 'UNKNOWN')}</span></header><strong>{fmt(item.current_price, 8, language)}</strong><dl><dt>{t(language, 'signalScore')}</dt><dd>{fmt(item.signal_score, 0, language)} / 10</dd><dt>{t(language, 'signalStrength')}</dt><dd>{strength === undefined ? '—' : `${fmt(strength * 100, 1, language)}%`}</dd><dt>{t(language, 'risk')}</dt><dd>{translateCode(language, item.risk_status ?? item.reason ?? '—')}</dd></dl></article> })}</div> : <Empty text={t(language, 'scanEmpty')} />}</Panel><DataTable title={t(language, 'signalHistory')} rows={data.signals} language={language} /></>
}

function Approval({ plans, executionReady, busy, revalidate, reject, language }: { plans: Plan[]; executionReady: boolean; busy: string; revalidate: (plan: Plan) => void; reject: (plan: Plan) => void; language: Language }) {
  return <Panel title={t(language, 'serverPlans')} eyebrow={t(language, 'freshRevalidation')}>{!plans.length ? <Empty text={t(language, 'noPlans')} /> : <div className="plan-list">{plans.map((plan) => { const pending = plan.ui_status === 'PENDING_APPROVAL', seconds = Math.max(0, Math.floor((plan.expires_at_ms - Date.now()) / 1000)), strength = number(plan.signal_strength); return <article className="plan-row" key={plan.plan_id}><div className="plan-symbol"><b>{plan.symbol}</b><code>{plan.plan_id}</code></div><div><small>{t(language, 'status')}</small><strong>{translateCode(language, plan.ui_status ?? plan.status)}</strong>{pending && <span>{language === 'zh' ? `剩余 ${seconds} 秒` : `${seconds}s TTL`}</span>}</div><div><small>{t(language, 'scoreStrength')}</small><strong>{fmt(plan.signal_score, 0, language)} / {strength === undefined ? '—' : `${fmt(strength * 100, 1, language)}%`}</strong></div><div><small>{t(language, 'entrySize')}</small><strong>{fmt(plan.entry, 8, language)} · {fmt(plan.position_size, 8, language)}</strong></div><div className="row-actions"><Button secondary disabled={!pending || Boolean(busy)} onClick={() => reject(plan)}>{t(language, 'reject')}</Button><Button disabled={!pending || !executionReady || Boolean(busy)} title={!executionReady ? t(language, 'executionRequirement') : undefined} onClick={() => revalidate(plan)}>{t(language, 'revalidateApprove')}</Button></div></article> })}</div>}</Panel>
}

function ChallengeCountdown({ expiresAt, language }: { expiresAt: number; language: Language }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 250); return () => window.clearInterval(timer) }, [])
  const seconds = Math.max(0, (expiresAt - now) / 1000)
  return <div className={`challenge-clock ${seconds <= 3 ? 'urgent' : ''}`}><Icon name="shield" /><span>{language === 'zh' ? '一次性审批授权剩余' : 'One-time approval expires in'}</span><b>{seconds.toFixed(1)}s</b></div>
}

const lifecycleStages = ['PLANNED', 'APPROVED', 'SUBMITTED', 'OPEN', 'PARTIALLY_FILLED', 'FILLED', 'PROTECTED', 'CLOSED']

function LifecycleRail({ state, protection, language }: { state: string; protection: string; language: Language }) {
  const effective = state === 'FILLED' && protection === 'PROTECTED' ? 'PROTECTED' : state
  const current = Math.max(0, lifecycleStages.indexOf(effective))
  return <div className="lifecycle-rail" aria-label={`${translateCode(language, state)} / ${translateCode(language, protection)}`}>{lifecycleStages.map((stage, index) => <span key={stage} className={index <= current ? 'done' : ''} title={translateCode(language, stage)} />)}</div>
}

function OrdersPanel({ value, language }: { value?: Record<string, unknown>; language: Language }) {
  const data = asRecord(value), rows = Array.isArray(data.agent_order_lifecycle) ? data.agent_order_lifecycle.map(asRecord) : []
  return <Panel title={t(language, 'persistentLifecycle')} eyebrow={t(language, 'coreStates')}>{rows.length ? <div className="table-wrap lifecycle-table"><table><thead><tr><th>{fieldLabel(language, 'symbol')}</th><th>{fieldLabel(language, 'plan_id')}</th><th>{fieldLabel(language, 'state')}</th><th>{fieldLabel(language, 'filled_size')}</th><th>{fieldLabel(language, 'protection_state')}</th><th>{language === 'zh' ? '生命周期' : 'Lifecycle'}</th></tr></thead><tbody>{rows.map((row, index) => { const state = String(row.state ?? 'UNKNOWN'), protection = String(row.protection_state ?? 'NOT_APPLICABLE'), danger = ['SUBMISSION_UNKNOWN', 'POSITION_UNPROTECTED'].includes(state) || ['PROTECTION_NOT_FOUND', 'TP_SL_BACKEND_NOT_SUPPORTED'].includes(protection); return <tr key={String(row.plan_id ?? index)} className={danger ? 'risk-row' : ''}><td><b>{String(row.symbol ?? '—')}</b></td><td><code>{String(row.plan_id ?? '—')}</code></td><td><span className={`state-tag ${danger ? 'danger' : ''}`}>{translateCode(language, state)}</span></td><td>{fmt(row.filled_size, 8, language)} / {fmt(row.requested_size, 8, language)}</td><td>{translateCode(language, protection)}</td><td><LifecycleRail state={state} protection={protection} language={language} /></td></tr> })}</tbody></table></div> : <Empty text={t(language, 'noOrders')} />}<details className="advanced-details"><summary>{language === 'zh' ? '原始调试数据' : 'Raw debug data'}</summary><ObjectView value={value} empty={t(language, 'noOrders')} language={language} /></details></Panel>
}

function PositionsPanel({ value, language }: { value?: Record<string, unknown>; language: Language }) {
  const data = asRecord(value), walletRaw = data.wallet_balances, wallet = Array.isArray(walletRaw) ? walletRaw.map(asRecord) : Object.entries(asRecord(walletRaw)).map(([currency, equity]) => ({ currency, equity })), managed = Array.isArray(data.managed_positions) ? data.managed_positions.map(asRecord) : []
  return <div className="two-column positions-split"><DataTable title={language === 'zh' ? '钱包资产' : 'Wallet Assets'} rows={wallet} language={language} /><DataTable title={language === 'zh' ? '代理管理仓位' : 'Agent Managed Positions'} rows={managed} language={language} /><Panel title={t(language, 'accountExposure')} eyebrow={t(language, 'exposureSemantics')}><ObjectView value={data.account_exposure} empty={t(language, 'dataUnavailable')} language={language} /></Panel><Panel title={language === 'zh' ? '仓位占用' : 'Position Slots'} eyebrow={t(language, 'walletExcluded')}><div className="metric-row compact"><Metric label={fieldLabel(language, 'managed_open_positions')} value={fmt(data.managed_open_positions, 0, language)} /><Metric label={fieldLabel(language, 'position_slots_in_use')} value={fmt(data.position_slots_in_use, 0, language)} /><Metric label={fieldLabel(language, 'reserved_entry_notional')} value={`$${fmt(data.reserved_entry_notional, 2, language)}`} /></div></Panel></div>
}

function DataTable({ title, rows, language }: { title: string; rows: Record<string, unknown>[]; language: Language }) {
  const keys = useMemo(() => Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 8), [rows])
  return <Panel title={title} eyebrow={`${rows.length} ${t(language, 'records')}`}>{rows.length ? <div className="table-wrap"><table><thead><tr>{keys.map((key) => <th key={key}>{fieldLabel(language, key)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id ?? row.plan_id ?? index)}>{keys.map((key) => <td key={key}><DisplayValue value={row[key]} language={language} /></td>)}</tr>)}</tbody></table></div> : <Empty text={t(language, 'noRecords')} />}</Panel>
}

function BacktestPanel({ result, symbol, days, busy, setSymbol, setDays, run, language }: { result: Record<string, unknown> | null; symbol: string; days: number; busy: string; setSymbol: (value: string) => void; setDays: (value: number) => void; run: (walk: boolean) => void; language: Language }) {
  const performance = asRecord(result?.performance), assumptions = asRecord(result?.assumptions), skip = asRecord(result?.skip_reason_breakdown)
  return <><Panel title={t(language, 'historicalEvaluation')} eyebrow={t(language, 'historicalSubtitle')}><div className="form-row"><label>{t(language, 'symbol')}<input value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} /></label><label>{t(language, 'range')}<select value={days} onChange={(event) => setDays(Number(event.target.value))}>{[7, 30, 90].map((value) => <option key={value} value={value}>{value} {t(language, 'days')}</option>)}</select></label><Button disabled={Boolean(busy)} onClick={() => run(false)}>{t(language, 'runBacktest')}</Button><Button secondary disabled={Boolean(busy)} onClick={() => run(true)}>{t(language, 'walkForward')}</Button></div></Panel>{result ? <><div className="metric-row compact backtest-metrics">{[['net_pnl', 2], ['profit_factor', 2], ['sharpe_ratio', 2], ['sortino_ratio', 2], ['maximum_drawdown', 3], ['win_rate', 3], ['average_win', 2], ['average_loss', 2], ['fees_paid', 2], ['slippage_cost', 2], ['total_trades', 0]].map(([key, digits]) => <Metric key={String(key)} label={fieldLabel(language, String(key))} value={['maximum_drawdown', 'win_rate'].includes(String(key)) ? `${fmt(Number(performance[String(key)]) * 100, Number(digits), language)}%` : fmt(performance[String(key)], Number(digits), language)} />)}</div><div className="two-column"><Curve title={language === 'zh' ? '权益曲线' : 'Equity Curve'} points={performance.equity_curve} /><Curve title={language === 'zh' ? '回撤曲线' : 'Drawdown Curve'} points={performance.drawdown_curve} percentage /></div><Panel title={language === 'zh' ? '执行假设与跳过原因' : 'Execution Assumptions & Skips'} eyebrow={`${fmt(result.skipped_after_execution_revalidation, 0, language)} ${language === 'zh' ? '次执行重验跳过' : 'execution skips'}`}><div className="two-column"><ObjectView value={assumptions} empty="—" language={language} /><ObjectView value={skip} empty="—" language={language} /></div><details className="advanced-details"><summary>{language === 'zh' ? '原始回测结果' : 'Raw backtest result'}</summary><ObjectView value={result} empty={t(language, 'evaluationEmpty')} language={language} /></details></Panel></> : <Panel title={t(language, 'result')} eyebrow={t(language, 'noTuning')}><Empty text={t(language, 'evaluationEmpty')} /></Panel>}</>
}

function Curve({ title, points, percentage = false }: { title: string; points: unknown; percentage?: boolean }) {
  const rows = Array.isArray(points) ? points.map(asRecord) : [], values = rows.map((row) => number(row.value)).filter((value): value is number => value !== undefined)
  if (values.length < 2) return <Panel title={title}><Empty text="—" /></Panel>
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1
  const path = values.map((value, index) => `${index ? 'L' : 'M'} ${(index / (values.length - 1) * 100).toFixed(2)} ${(38 - ((value - min) / range) * 34).toFixed(2)}`).join(' ')
  return <Panel title={title} eyebrow={`${percentage ? (max * 100).toFixed(2) + '%' : max.toFixed(2)} / ${percentage ? (min * 100).toFixed(2) + '%' : min.toFixed(2)}`}><svg className="curve-chart" viewBox="0 0 100 40" role="img" aria-label={title} preserveAspectRatio="none"><path className="curve-area" d={`${path} L 100 40 L 0 40 Z`} /><path className="curve-line" d={path} /></svg></Panel>
}

function LogsPanel({ logs, audit, language }: { logs: string[]; audit: Record<string, unknown>[]; language: Language }) {
  return <div className="two-column"><Panel title={t(language, 'runtimeLog')} eyebrow={`${logs.length} ${t(language, 'lines')}`}><div className="log-view">{logs.length ? logs.map((line, index) => <code key={index}>{line}</code>) : <Empty text={t(language, 'noLogs')} />}</div></Panel><Panel title={t(language, 'transitionAudit')} eyebrow={`${audit.length} ${t(language, 'events')}`}><div className="audit-list">{audit.length ? audit.map((item, index) => <article key={String(item.id ?? index)}><small>{stamp(item.timestamp_ms, language)}</small><b>{translateCode(language, item.requested_action)}</b><span>{translateCode(language, item.reason)}</span></article>) : <Empty text={t(language, 'noAudit')} />}</div></Panel></div>
}

function SettingsPanel({ data, control, stream, highRiskReady, busy, setMode, act, language, theme, setLanguage, setTheme }: { data: DashboardData; control?: ControlState; stream: Connection; highRiskReady: boolean; busy: string; setMode: (mode: TradingMode) => void; act: (name: string, callback: () => Promise<unknown>) => Promise<void>; language: Language; theme: ThemePreference; setLanguage: (value: Language) => void; setTheme: (value: ThemePreference) => void }) {
  const [interval, setIntervalValue] = useState(control?.scan_interval_seconds ?? 15), [autoText, setAutoText] = useState(''), [resetText, setResetText] = useState('')
  useEffect(() => setIntervalValue(control?.scan_interval_seconds ?? 15), [control?.scan_interval_seconds])
  return <><Panel title={t(language, 'appearance')} eyebrow={t(language, 'appearanceSubtitle')}><div className="appearance-settings"><div><Icon name="language"/><span><b>{t(language, 'language')}</b><small>{language === 'zh' ? t(language, 'chinese') : t(language, 'english')}</small></span></div><PreferenceControls language={language} theme={theme} setLanguage={setLanguage} setTheme={setTheme} /></div></Panel><Panel title={t(language, 'runtimeControls')} eyebrow={t(language, 'auditedTransitions')}><div className="control-stack">
    <div className="control-line"><div><small>{t(language, 'environment')}</small><b>{translateCode(language, 'DEMO')}</b><p>{t(language, 'liveNotConfigured')}</p></div><button className="segmented active">{translateCode(language, 'DEMO')}</button><button className="segmented" disabled>{translateCode(language, 'LIVE')} · {translateCode(language, 'LOCKED')}</button></div>
    <div className="control-line"><div><small>{t(language, 'tradingMode')}</small><b>{translateCode(language, control?.trading_mode ?? 'STOPPED')}</b><p>{t(language, 'modeIndependent')}</p></div><div className="segmented-control">{(['STOPPED', 'DRY_RUN', 'MANUAL_APPROVAL', 'AUTO'] as TradingMode[]).map((mode) => <button key={mode} className={`segmented ${control?.trading_mode === mode ? 'active' : ''}`} disabled={Boolean(busy) || (mode === 'AUTO' && (!control?.auto_demo_enabled || stream !== 'CONNECTED'))} onClick={() => setMode(mode)}>{translateCode(language, mode)}</button>)}</div></div>
    <div className="control-line"><div><small>{t(language, 'agentRuntime')}</small><b>{translateCode(language, control?.agent_runtime_state ?? 'STOPPED')}</b><p>{t(language, 'workerDescription')}</p></div><Button disabled={Boolean(busy) || control?.trading_mode === 'STOPPED'} onClick={() => void act(t(language, 'startAgent'), () => write('/agent/start'))}>{t(language, 'start')}</Button><Button secondary disabled={Boolean(busy)} onClick={() => void act(t(language, 'stopAgent'), () => write('/agent/stop'))}>{t(language, 'stop')}</Button></div>
    <div className="control-line"><div><small>{t(language, 'demoExecution')}</small><b>{translateCode(language, control?.execution_state ?? 'DISARMED')}</b><p>{t(language, 'armRestart')}</p></div><Button disabled={!highRiskReady || Boolean(busy) || control?.agent_runtime_state !== 'RUNNING' || control?.trading_mode === 'DRY_RUN' || control?.trading_mode === 'STOPPED'} onClick={() => void act(t(language, 'armExecution'), () => write('/execution/arm'))}>{t(language, 'arm')}</Button><Button secondary disabled={Boolean(busy)} onClick={() => void act(t(language, 'disarmExecution'), () => write('/execution/disarm'))}>{t(language, 'disarm')}</Button></div>
  </div></Panel><div className="two-column"><Panel title={t(language, 'autoDemo')} eyebrow={t(language, 'sessionOnly')}><p className="panel-copy">{t(language, 'autoDescription')}</p><label className="confirmation-field">{t(language, 'typePhrase')} <code>ENABLE AUTO DEMO</code><input value={autoText} onChange={(event) => setAutoText(event.target.value)} /></label><div className="button-row"><Button disabled={autoText !== 'ENABLE AUTO DEMO' || !highRiskReady || Boolean(busy)} onClick={() => void act(t(language, 'enableAuto'), () => write('/auto-demo/enable', { confirmation: autoText }))}>{t(language, 'enable')}</Button><Button secondary disabled={Boolean(busy)} onClick={() => void act(t(language, 'disableAuto'), () => write('/auto-demo/disable'))}>{t(language, 'disable')}</Button></div></Panel><Panel title={t(language, 'killSwitch')} eyebrow={translateCode(language, control?.kill_switch_active ? 'ACTIVE' : 'OFF')} className="danger-panel"><p className="panel-copy">{t(language, 'killDescription')}</p><div className="button-row"><Button danger disabled={Boolean(busy)} onClick={() => { if (window.confirm(t(language, 'killConfirm'))) void act(t(language, 'killSwitch'), () => write('/kill-switch')) }}>{t(language, 'activateKill')}</Button></div>{control?.kill_switch_active && <><label className="confirmation-field">{t(language, 'typePhrase')} <code>RESET KILL SWITCH</code><input value={resetText} onChange={(event) => setResetText(event.target.value)} /></label><Button secondary disabled={resetText !== 'RESET KILL SWITCH' || Boolean(busy)} onClick={() => void act(t(language, 'resetKill'), () => write('/kill-switch/reset', { confirmation: resetText }))}>{t(language, 'resetSafe')}</Button></>}</Panel></div><Panel title={t(language, 'runtimeSettings')} eyebrow={t(language, 'allowlistOnly')}><div className="form-row"><label>{t(language, 'scanInterval')}<input type="number" min={5} max={3600} value={interval} onChange={(event) => setIntervalValue(Number(event.target.value))} /></label><Button secondary disabled={Boolean(busy)} onClick={() => void act(t(language, 'updateInterval'), () => write('/settings/runtime', { scan_interval_seconds: interval }, 'PUT'))}>{t(language, 'saveInterval')}</Button></div><details className="advanced-details"><summary>{language === 'zh' ? '查看核心配置' : 'View core configuration'}</summary><ObjectView value={data.settings} empty={t(language, 'dataUnavailable')} language={language} /></details></Panel></>
}

export default App
