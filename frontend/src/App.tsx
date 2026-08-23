import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ApiError, createSession, loadDashboard, websocketUrl, write } from './api'
import {
  activeAgentEntries,
  ApprovalCenter,
  asRecord,
  copy,
  FlattenDialog,
  GenericTable,
  LogsAuditView,
  MarketSignals,
  ObjectView,
  OrderLifecycle,
  OverviewMetrics,
  Panel,
  PositionsView,
  protectionSummary,
  SessionControlCard,
  SettingsView,
  StopDialog,
  TradesView,
} from './dashboardViews'
import { formatTimestamp } from './formatters'
import { t, translateCode, type Language, type TextKey } from './locales'
import type { Connection, ControlState, DashboardData, Plan, SessionState, StreamEvent } from './types'

type Section = 'overview' | 'scanner' | 'approval' | 'orders' | 'positions' | 'trades' | 'backtest' | 'logs' | 'settings'
export type ThemePreference = 'system' | 'light' | 'dark'
type IconName = Section | 'shield' | 'language' | 'appearance' | 'close' | 'lock'
type Confirmation = 'stop' | 'flatten' | null

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

const emptyData: DashboardData = { signals: [], plans: [], fills: [], trades: [], logs: [], audit: [] }

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

function errorCode(error: unknown): string {
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
  }
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function StatusPill({ label, value, tone = 'muted' }: { label: string; value: string; tone?: string }) {
  return <span className={`status-pill ${tone}`}><i /><span><small>{label}</small><b>{value}</b></span></span>
}

export function SafetyBar({ control, stream, language = 'en' }: { control?: ControlState; stream: Connection; language?: Language }) {
  const environment = control?.environment ?? 'DEMO'
  return <header className="topbar" aria-label={copy(language, '交易安全状态', 'Safety status')}>
    <div className="brand-lockup"><span className="brand-mark">OKX</span><span><b>{t(language, 'product')}</b><small>{t(language, 'localControl')}</small></span></div>
    <div className="safety-states">
      <StatusPill label={t(language, 'environment')} value={translateCode(language, environment)} tone={environment === 'DEMO' ? 'blue' : 'danger'} />
      <StatusPill label={t(language, 'mode')} value={translateCode(language, control?.trading_mode ?? 'STOPPED')} />
      <StatusPill label={t(language, 'execution')} value={translateCode(language, control?.execution_state ?? 'DISARMED')} tone={control?.execution_state === 'ARMED' ? 'amber' : 'muted'} />
      <StatusPill label={t(language, 'killSwitch')} value={translateCode(language, control?.kill_switch_active ? 'ACTIVE' : 'OFF')} tone={control?.kill_switch_active ? 'danger' : 'muted'} />
      <StatusPill label={t(language, 'stream')} value={translateCode(language, stream)} tone={stream === 'CONNECTED' ? 'green' : stream === 'STALE' ? 'amber' : 'danger'} />
    </div>
  </header>
}

export function LiveLockPanel({ language }: { language: Language }) {
  return <div className="live-lock" data-severity="normal"><Icon name="lock" /><div><b>{t(language, 'liveLocked')}</b><p>{copy(language, '实盘交易 · 已锁定 / 尚未实现', 'Live Trading · LOCKED / NOT IMPLEMENTED')}</p></div></div>
}

function PreferenceControls({ language, theme, setLanguage, setTheme }: { language: Language; theme: ThemePreference; setLanguage: (value: Language) => void; setTheme: (value: ThemePreference) => void }) {
  return <div className="preference-controls compact">
    <div className="preference-group" role="group" aria-label={t(language, 'language')}><Icon name="language" size={16} />{(['zh', 'en'] as Language[]).map((value) => <button key={value} className={language === value ? 'selected' : ''} onClick={() => setLanguage(value)}>{value === 'zh' ? '中文' : 'EN'}</button>)}</div>
    <div className="preference-group theme-group" role="group" aria-label={t(language, 'theme')}><Icon name="appearance" size={16} />{(['system', 'light', 'dark'] as ThemePreference[]).map((value) => <button key={value} className={theme === value ? 'selected' : ''} onClick={() => setTheme(value)}>{t(language, value)}</button>)}</div>
  </div>
}

function ApprovalDialog({ preview, language, onCancel, onApprove }: { preview: Record<string, unknown>; language: Language; onCancel: () => void; onApprove: () => void }) {
  return <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="approval-title"><header><div><small>{copy(language, '最新实时复核预览', 'Fresh revalidation preview')}</small><h2 id="approval-title">{copy(language, '批准模拟盘交易', 'Approve Demo trade')}</h2></div></header><ObjectView value={preview} language={language} empty="—" /><p className="semantic-note">{copy(language, '提交审批仍由后端挑战校验、风险管理器和订单管理器最终裁决。', 'The backend Challenge, RiskManager, and OrderManager remain authoritative.')}</p><div className="modal-actions"><button className="button secondary" onClick={onCancel}>{copy(language, '取消', 'Cancel')}</button><button className="button" onClick={onApprove}>{copy(language, '批准模拟盘交易', 'Approve Demo trade')}</button></div></section></div>
}

function BacktestView({ language, busy, result, onRun }: { language: Language; busy: boolean; result: Record<string, unknown> | null; onRun: (symbol: string, days: number, walkForward: boolean) => void }) {
  const [symbol, setSymbol] = useState('BTC-USDT')
  const [days, setDays] = useState(7)
  return <><Panel title={copy(language, '历史回测评估', 'Historical Evaluation')} eyebrow={copy(language, '研究用途 · 不进入实时执行路径', 'Research only · outside realtime execution')}><p className="panel-copy">{copy(language, '生产策略：scalping_v1_baseline。本轮不进行参数调优，回测参数与生产风控保持分离。', 'Production strategy: scalping_v1_baseline. No parameter tuning is performed; research inputs remain separate from production risk.')}</p><div className="form-row"><label>{copy(language, '交易对', 'Symbol')}<select value={symbol} onChange={(event) => setSymbol(event.target.value)}>{['BTC-USDT', 'ETH-USDT', 'SOL-USDT'].map((item) => <option key={item}>{item}</option>)}</select></label><label>{copy(language, '范围', 'Range')}<select value={days} onChange={(event) => setDays(Number(event.target.value))}>{[7, 30, 90].map((item) => <option key={item} value={item}>{item} {copy(language, '天', 'days')}</option>)}</select></label><button className="button" disabled={busy} onClick={() => onRun(symbol, days, false)}>{copy(language, '运行回测', 'Run backtest')}</button><button className="button secondary" disabled={busy} onClick={() => onRun(symbol, days, true)}>{copy(language, '滚动前向验证', 'Walk-forward')}</button></div></Panel>{result && <Panel title={copy(language, '回测结果', 'Backtest Result')} eyebrow={formatTimestamp(result.generated_at_ms, language)}><ObjectView value={result} language={language} empty="—" /></Panel>}</>
}

function App() {
  const [data, setData] = useState<DashboardData>(emptyData)
  const [stream, setStream] = useState<Connection>('DISCONNECTED')
  const [section, setSection] = useState<Section>('overview')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [confirmation, setConfirmation] = useState<Confirmation>(null)
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)
  const [backtest, setBacktest] = useState<Record<string, unknown> | null>(null)
  const [language, setLanguageState] = useState<Language>(() => savedPreference('okx-language', ['zh', 'en'], 'zh'))
  const [theme, setThemeState] = useState<ThemePreference>(() => savedPreference('okx-dashboard-theme-v2', ['system', 'light', 'dark'], 'dark'))
  const reconnect = useRef<number | undefined>(undefined)

  const setLanguage = (value: Language) => { setLanguageState(value); persistPreference('okx-language', value) }
  const setTheme = (value: ThemePreference) => { setThemeState(value); persistPreference('okx-dashboard-theme-v2', value) }
  const refresh = useCallback(async () => setData(await loadDashboard()), [])
  const control = data.status?.control
  const sessionState = (control?.session_state ?? 'STOPPED') as SessionState
  const session = asRecord(data.session ?? data.status?.session)
  const positions = asRecord(data.positions)
  const managedPositions = useMemo(() => Array.isArray(positions.managed_positions) ? positions.managed_positions.map(asRecord) : [], [positions.managed_positions])
  const externalInventory = useMemo(() => Array.isArray(positions.external_wallet_inventory) ? positions.external_wallet_inventory.map(asRecord) : [], [positions.external_wallet_inventory])
  const managedCount = Number(positions.managed_open_positions ?? managedPositions.length)
  const activeEntries = activeAgentEntries(data.orders)
  const protection = protectionSummary(managedPositions)
  const writesReady = highRiskWritesAllowed(control, stream)
  const executionReady = writesReady && control?.execution_state === 'ARMED' && control?.agent_runtime_state === 'RUNNING'
  const pending = data.plans.filter((plan) => plan.ui_status === 'PENDING_APPROVAL')
  const currentPage = navigation.find((item) => item.id === section) ?? navigation[0]

  useEffect(() => {
    const media = window.matchMedia?.('(prefers-color-scheme: dark)')
    const apply = () => { const resolved = resolveTheme(theme, media?.matches ?? false); document.documentElement.dataset.theme = resolved; document.documentElement.style.colorScheme = resolved }
    apply(); media?.addEventListener?.('change', apply)
    return () => media?.removeEventListener?.('change', apply)
  }, [theme])

  useEffect(() => { document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en' }, [language])

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
      socket.onclose = () => { setStream('DISCONNECTED'); if (active) reconnect.current = window.setTimeout(() => void createSession().then(connect), 1500) }
      socket.onerror = () => setStream('DISCONNECTED')
    }
    void createSession().then(() => { if (active) { connect(); void refresh() } }).catch((error) => setNotice({ tone: 'error', text: errorCode(error) }))
    return () => { active = false; window.clearTimeout(reconnect.current); socket?.close() }
  }, [refresh])

  const runAction = async <T,>(name: string, callback: () => Promise<T>, onSuccess?: (value: T) => void) => {
    setBusy(name); setNotice(null)
    try { const value = await callback(); onSuccess?.(value); await refresh(); setNotice({ tone: 'ok', text: `${name} ${copy(language, '已完成', 'completed')}` }) }
    catch (error) { setNotice({ tone: 'error', text: translateCode(language, errorCode(error)) }) }
    finally { setBusy('') }
  }
  const start = () => { if (window.confirm(copy(language, '启动自动模拟盘交易会话？', 'Start the automatic Demo trading session?'))) void runAction(copy(language, '启动', 'Start'), () => write('/session/start')) }
  const pause = () => void runAction(copy(language, '暂停', 'Pause'), () => write('/session/pause'))
  const stop = () => { setConfirmation(null); void runAction(copy(language, '停止', 'Stop'), () => write('/session/stop')) }
  const flatten = () => { setConfirmation(null); void runAction<Record<string, unknown>>(copy(language, '全部平仓', 'Flatten'), () => write('/session/flatten'), (value) => setData((current) => ({ ...current, session: value }))) }
  const previewPlan = (plan: Plan) => void runAction<Record<string, unknown>>(copy(language, '审批预览', 'Approval preview'), () => write(`/plans/${encodeURIComponent(plan.plan_id)}/preview`), setPreview)
  const approvePlan = () => { const planId = String(preview?.plan_id ?? ''); void runAction(copy(language, '模拟盘审批', 'Demo approval'), () => write(`/plans/${encodeURIComponent(planId)}/approve`, { approval_challenge: preview?.approval_challenge }), () => setPreview(null)) }

  const sessionCard = <SessionControlCard state={sessionState} session={session} market={asRecord(data.market ?? data.status?.market)} protection={protection} managedCount={managedCount} activeEntries={activeEntries} writesReady={writesReady} busy={Boolean(busy)} onStart={start} onPause={pause} onStop={() => setConfirmation('stop')} onFlatten={() => setConfirmation('flatten')} language={language} compact={section !== 'overview'} />

  return <div className="app-shell">
    <SafetyBar control={control} stream={stream} language={language} />
    <aside className="sidebar"><nav aria-label={copy(language, '主导航', 'Primary navigation')}>{navigation.map((item) => <button key={item.id} className={section === item.id ? 'active' : ''} aria-label={t(language, item.label)} title={t(language, item.label)} onClick={() => setSection(item.id)}><Icon name={item.id} /><span>{t(language, item.label)}</span>{item.id === 'approval' && pending.length > 0 && <b className="nav-badge">{pending.length}</b>}</button>)}</nav><LiveLockPanel language={language} /></aside>
    <main className="main-content"><div className="page-heading"><div><span className="page-kicker">OKX {translateCode(language, 'DEMO')} <i /> {t(language, 'localOnly')}</span><h1>{t(language, currentPage.label)}</h1><p>{t(language, currentPage.subtitle)}</p></div><PreferenceControls language={language} theme={theme} setLanguage={setLanguage} setTheme={setTheme} /></div>
      {notice && <div className={`notice ${notice.tone}`} role="status"><span>{notice.text}</span><button aria-label={t(language, 'dismiss')} onClick={() => setNotice(null)}><Icon name="close" size={16} /></button></div>}
      {stream !== 'CONNECTED' && <div className="stale-banner"><Icon name="shield" /><div><b>{t(language, 'writeLock')}</b><span>{t(language, 'writeLockDetail')}</span></div></div>}
      <div className="global-session-control">{sessionCard}</div>
      <div className="page-content" key={`${section}-${language}`}>
        {section === 'overview' && <><OverviewMetrics data={data} language={language} /><div className="two-column"><Panel title={t(language, 'tradingEligibility')} eyebrow={copy(language, '风险管理器最终裁决', 'RiskManager remains authoritative')}><ObjectView value={asRecord(data.health).trading_eligibility} language={language} empty={t(language, 'dataUnavailable')} /></Panel><Panel title={copy(language, '运行状态', 'Runtime Posture')} eyebrow={copy(language, '独立安全域', 'Independent safety domains')}><ObjectView value={{ agent: control?.agent_runtime_state, mode: control?.trading_mode, execution: control?.execution_state, kill_switch: control?.kill_switch_active }} language={language} empty="—" /></Panel></div><Panel title={t(language, 'accountExposure')} eyebrow={t(language, 'exposureSubtitle')}><ObjectView value={asRecord(data.account).exposure ?? positions.account_exposure} language={language} empty={t(language, 'dataUnavailable')} /></Panel></>}
        {section === 'scanner' && <MarketSignals data={data} language={language} />}
        {section === 'approval' && <ApprovalCenter plans={data.plans} sessionState={sessionState} executionReady={Boolean(executionReady)} busy={Boolean(busy)} onPreview={previewPlan} onReject={(plan) => void runAction(copy(language, '拒绝计划', 'Reject plan'), () => write(`/plans/${encodeURIComponent(plan.plan_id)}/reject`))} language={language} />}
        {section === 'orders' && <><OrderLifecycle value={data.orders} language={language} /><GenericTable title={copy(language, '成交记录', 'Fills')} rows={data.fills} language={language} /></>}
        {section === 'positions' && <PositionsView value={data.positions} settings={data.settings} language={language} />}
        {section === 'trades' && <TradesView rows={data.trades} language={language} />}
        {section === 'backtest' && <BacktestView language={language} busy={Boolean(busy)} result={backtest} onRun={(symbol, days, walkForward) => void runAction<Record<string, unknown>>(copy(language, '回测', 'Backtest'), () => write('/backtest', { symbol, days, walk_forward: walkForward }), setBacktest)} />}
        {section === 'logs' && <LogsAuditView logs={data.logs} audit={data.audit} language={language} />}
        {section === 'settings' && <SettingsView data={data.settings} control={control} language={language} />}
      </div>
    </main>
    {confirmation === 'stop' && <StopDialog language={language} onCancel={() => setConfirmation(null)} onConfirm={stop} />}
    {confirmation === 'flatten' && <FlattenDialog positions={managedPositions} externalCount={externalInventory.length} language={language} onCancel={() => setConfirmation(null)} onConfirm={flatten} />}
    {preview && <ApprovalDialog preview={preview} language={language} onCancel={() => setPreview(null)} onApprove={approvePlan} />}
  </div>
}

export default App
