import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, createSession, get, loadDashboard, websocketUrl, write } from './api'
import type { Connection, ControlState, DashboardData, Plan, StreamEvent, TradingMode } from './types'

type Section = 'overview' | 'scanner' | 'approval' | 'orders' | 'positions' | 'trades' | 'backtest' | 'logs' | 'settings'

const navigation: { id: Section; label: string; eyebrow: string }[] = [
  { id: 'overview', label: 'Overview', eyebrow: '01' },
  { id: 'scanner', label: 'Market & Signals', eyebrow: '02' },
  { id: 'approval', label: 'Approval Center', eyebrow: '03' },
  { id: 'orders', label: 'Order Lifecycle', eyebrow: '04' },
  { id: 'positions', label: 'Positions', eyebrow: '05' },
  { id: 'trades', label: 'Trades', eyebrow: '06' },
  { id: 'backtest', label: 'Backtest Lab', eyebrow: '07' },
  { id: 'logs', label: 'Logs & Audit', eyebrow: '08' },
  { id: 'settings', label: 'Settings', eyebrow: '09' },
]

const emptyData: DashboardData = { signals: [], plans: [], trades: [], logs: [], audit: [] }

export function highRiskWritesAllowed(control: ControlState | undefined, stream: Connection): boolean {
  return stream === 'CONNECTED' && control?.connection_state === 'CONNECTED' && !control.kill_switch_active
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function number(value: unknown): number | undefined {
  return typeof value === 'number' ? value : typeof value === 'string' && value !== '' ? Number(value) : undefined
}

function fmt(value: unknown, digits = 2): string {
  const parsed = number(value)
  return parsed !== undefined && Number.isFinite(parsed) ? parsed.toLocaleString(undefined, { maximumFractionDigits: digits }) : '—'
}

function stamp(value: unknown): string {
  const parsed = number(value)
  return parsed ? new Date(parsed).toLocaleString() : '—'
}

function reason(error: unknown): string {
  if (error instanceof ApiError) return error.code
  return error instanceof Error ? error.message : 'UNKNOWN_ERROR'
}

export function SafetyBar({ control, stream }: { control?: ControlState; stream: Connection }) {
  const environment = control?.environment ?? 'DEMO'
  return (
    <div className="safety-bar" aria-label="Safety status">
      <div className="brand-lockup"><span className="brand-mark">OX</span><span>Agent Control</span><small>v2.1</small></div>
      <div className="safety-states">
        <StatusPill label="Environment" value={environment} tone={environment === 'DEMO' ? 'blue' : 'danger'} />
        <StatusPill label="Mode" value={control?.trading_mode ?? 'STOPPED'} />
        <StatusPill label="Execution" value={control?.execution_state ?? 'DISARMED'} tone={control?.execution_state === 'ARMED' ? 'amber' : 'muted'} />
        <StatusPill label="Stream" value={stream} tone={stream === 'CONNECTED' ? 'green' : stream === 'STALE' ? 'amber' : 'danger'} />
      </div>
    </div>
  )
}

function StatusPill({ label, value, tone = 'muted' }: { label: string; value: string; tone?: string }) {
  return <span className={`status-pill ${tone}`}><small>{label}</small><b><i />{value}</b></span>
}

function Panel({ title, eyebrow, children, actions }: { title: string; eyebrow?: string; children: React.ReactNode; actions?: React.ReactNode }) {
  return <section className="panel"><header><div><small>{eyebrow}</small><h2>{title}</h2></div>{actions}</header>{children}</section>
}

function JsonView({ value, empty = 'No data available' }: { value: unknown; empty?: string }) {
  if (value === undefined || value === null || (Array.isArray(value) && value.length === 0)) return <Empty text={empty} />
  return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>
}

function Empty({ text }: { text: string }) {
  return <div className="empty"><span>∅</span><p>{text}</p></div>
}

function Button({ children, danger, secondary, disabled, onClick, title }: {
  children: React.ReactNode; danger?: boolean; secondary?: boolean; disabled?: boolean;
  onClick: () => void; title?: string
}) {
  return <button className={`button ${danger ? 'danger' : ''} ${secondary ? 'secondary' : ''}`} disabled={disabled} onClick={onClick} title={title}>{children}</button>
}

function App() {
  const [section, setSection] = useState<Section>('overview')
  const [data, setData] = useState<DashboardData>(emptyData)
  const [stream, setStream] = useState<Connection>('DISCONNECTED')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)
  const [approvalText, setApprovalText] = useState('')
  const [backtest, setBacktest] = useState<Record<string, unknown> | null>(null)
  const [backtestSymbol, setBacktestSymbol] = useState('BTC-USDT')
  const [backtestDays, setBacktestDays] = useState(7)
  const lastMessage = useRef(0)
  const reconnectTimer = useRef<number | undefined>(undefined)
  const refreshTimer = useRef<number | undefined>(undefined)

  const refresh = useCallback(async () => {
    const next = await loadDashboard()
    setData(next)
  }, [])

  useEffect(() => {
    let active = true
    let socket: WebSocket | null = null

    const scheduleRefresh = () => {
      window.clearTimeout(refreshTimer.current)
      refreshTimer.current = window.setTimeout(() => void refresh().catch(() => undefined), 300)
    }

    const connect = () => {
      if (!active) return
      socket = new WebSocket(websocketUrl())
      socket.onopen = () => {
        lastMessage.current = Date.now()
        setStream('CONNECTED')
      }
      socket.onmessage = (message) => {
        lastMessage.current = Date.now()
        setStream('CONNECTED')
        const event = JSON.parse(message.data) as StreamEvent
        if (event.type === 'snapshot') {
          const snapshot = event.data as unknown as DashboardData
          setData((current) => ({ ...current, ...snapshot }))
          return
        }
        if (event.type === 'control.state') {
          setData((current) => current.status ? ({
            ...current,
            status: { ...current.status, control: event.data as unknown as ControlState },
          }) : current)
          return
        }
        if (event.type === 'health.updated') {
          setData((current) => ({ ...current, health: event.data }))
          return
        }
        if (event.type === 'account.updated') {
          setData((current) => ({ ...current, account: event.data }))
          return
        }
        if (event.type === 'scanner.updated') {
          setData((current) => ({ ...current, scanner: event.data }))
          return
        }
        if (event.type !== 'heartbeat') scheduleRefresh()
      }
      socket.onerror = () => setStream('DISCONNECTED')
      socket.onclose = () => {
        setStream('DISCONNECTED')
        if (active) reconnectTimer.current = window.setTimeout(() => {
          void createSession().then(connect).catch(() => {
            if (active) reconnectTimer.current = window.setTimeout(connect, 2000)
          })
        }, 2000)
      }
    }

    void (async () => {
      try {
        await createSession()
        if (active) connect()
        const incremental: [string, keyof DashboardData, unknown][] = [
          ['/status', 'status', undefined], ['/health', 'health', {}],
          ['/account', 'account', {}], ['/scanner', 'scanner', {}],
          ['/signals?limit=100', 'signals', []], ['/plans?limit=100', 'plans', []],
          ['/orders', 'orders', {}], ['/positions', 'positions', {}],
          ['/trades', 'trades', []], ['/logs?limit=200', 'logs', []],
          ['/audit-log?limit=100', 'audit', []], ['/settings', 'settings', {}],
        ]
        for (const [path, key, fallback] of incremental) {
          void get<unknown>(path)
            .then((value) => setData((current) => ({ ...current, [key]: value })))
            .catch(() => setData((current) => ({ ...current, [key]: fallback })))
        }
      } catch (error) {
        setNotice({ tone: 'error', text: `Startup failed: ${reason(error)}` })
      }
    })()

    const staleClock = window.setInterval(() => {
      if (lastMessage.current && Date.now() - lastMessage.current > 7000) setStream('STALE')
    }, 1000)
    return () => {
      active = false
      window.clearInterval(staleClock)
      window.clearTimeout(reconnectTimer.current)
      window.clearTimeout(refreshTimer.current)
      socket?.close()
    }
  }, [refresh])

  const control = data.status?.control
  const highRiskReady = highRiskWritesAllowed(control, stream)
  const executionReady = highRiskReady && control?.execution_state === 'ARMED' && control?.agent_runtime_state === 'RUNNING'
  const pending = useMemo(() => data.plans.filter((plan) => plan.ui_status === 'PENDING_APPROVAL'), [data.plans])

  const act = async (name: string, callback: () => Promise<unknown>) => {
    setBusy(name)
    setNotice(null)
    try {
      await callback()
      await refresh()
      setNotice({ tone: 'ok', text: `${name} completed` })
    } catch (error) {
      setNotice({ tone: 'error', text: `${name}: ${reason(error)}` })
    } finally {
      setBusy('')
    }
  }

  const setMode = (mode: TradingMode) => void act(`Mode ${mode}`, () => write('/mode', { mode }))
  const revalidate = (plan: Plan) => void act('Plan revalidation', async () => {
    const result = await write<Record<string, unknown>>(`/plans/${encodeURIComponent(plan.plan_id)}/preview`)
    setPreview(result)
    setApprovalText('')
  })
  const approve = () => {
    const planId = String(preview?.plan_id ?? '')
    void act('Demo approval', async () => {
      await write(`/plans/${encodeURIComponent(planId)}/approve`, { confirmation: approvalText })
      setPreview(null)
    })
  }

  return (
    <div className="app-shell">
      <SafetyBar control={control} stream={stream} />
      <aside>
        <div className="side-intro"><small>Local control plane</small><strong>Capital first.<br />Automation second.</strong></div>
        <nav>{navigation.map((item) => <button key={item.id} className={section === item.id ? 'active' : ''} onClick={() => setSection(item.id)}><span>{item.eyebrow}</span>{item.label}</button>)}</nav>
        <div className="live-lock"><span>LIVE</span><b>Locked</b><p>W8 is not configured. No Live execution path exists.</p></div>
      </aside>
      <main>
        <div className="page-heading">
          <div><small>OKX DEMO · LOCALHOST</small><h1>{navigation.find((item) => item.id === section)?.label}</h1></div>
          <div className="heading-meta"><span>Authoritative core</span><b>Python / single worker</b></div>
        </div>
        {notice && <div className={`notice ${notice.tone}`} role="status">{notice.text}<button onClick={() => setNotice(null)}>×</button></div>}
        {stream !== 'CONNECTED' && <div className="stale-banner"><b>Write lock active</b> WebSocket is {stream.toLowerCase()}. ARM, approval and AUTO controls are disabled.</div>}

        {section === 'overview' && <Overview data={data} control={control} pending={pending.length} />}
        {section === 'scanner' && <Scanner data={data} busy={busy} run={() => void act('Market scan', () => write('/scanner/run'))} />}
        {section === 'approval' && <Approval plans={data.plans} executionReady={Boolean(executionReady)} busy={busy} revalidate={revalidate} reject={(plan) => void act('Plan rejection', () => write(`/plans/${encodeURIComponent(plan.plan_id)}/reject`))} />}
        {section === 'orders' && <Panel title="Persistent order lifecycle" eyebrow="Core states · SQLite"><JsonView value={data.orders} empty="No agent or OKX open orders" /></Panel>}
        {section === 'positions' && <Panel title="Wallet & managed positions" eyebrow="Separated exposure semantics"><JsonView value={data.positions} /></Panel>}
        {section === 'trades' && <DataTable title="Recorded trades" rows={data.trades} />}
        {section === 'backtest' && <BacktestPanel result={backtest} symbol={backtestSymbol} days={backtestDays} busy={busy} setSymbol={setBacktestSymbol} setDays={setBacktestDays} run={(walkForward) => void act(walkForward ? 'Walk-forward' : 'Backtest', async () => setBacktest(await write('/backtest', { symbol: backtestSymbol, days: backtestDays, walk_forward: walkForward }) as Record<string, unknown>))} />}
        {section === 'logs' && <LogsPanel logs={data.logs} audit={data.audit} />}
        {section === 'settings' && <SettingsPanel data={data} control={control} stream={stream} highRiskReady={Boolean(highRiskReady)} busy={busy} setMode={setMode} act={act} refresh={refresh} />}
      </main>

      {preview && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Approve Demo Trade">
        <div className="modal">
          <header><div><small>FRESH REVALIDATION PREVIEW</small><h2>Approve Demo Trade</h2></div><button onClick={() => setPreview(null)}>×</button></header>
          <div className="preview-grid">
            <Metric label="Symbol" value={preview.symbol} />
            <Metric label="Executable price" value={fmt(preview.current_executable_price, 8)} />
            <Metric label="Position size" value={fmt(preview.position_size, 8)} />
            <Metric label="Risk amount" value={`$${fmt(preview.final_risk_amount)}`} />
            <Metric label="Stop" value={fmt(preview.final_stop, 8)} />
            <Metric label="Take profit" value={fmt(preview.final_take_profit, 8)} />
          </div>
          <label className="confirmation-field">Type <code>CONFIRM DEMO ORDER</code><input value={approvalText} onChange={(event) => setApprovalText(event.target.value)} autoComplete="off" /></label>
          <div className="modal-actions"><Button secondary onClick={() => setPreview(null)}>Cancel</Button><Button disabled={approvalText !== 'CONFIRM DEMO ORDER' || !executionReady || Boolean(busy)} onClick={approve}>Submit Demo order</Button></div>
        </div>
      </div>}
    </div>
  )
}

function Overview({ data, control, pending }: { data: DashboardData; control?: ControlState; pending: number }) {
  const account = asRecord(data.account)
  const positions = asRecord(data.positions)
  const eligibility = asRecord(asRecord(data.health).trading_eligibility)
  return <>
    <div className="metric-row">
      <Metric label="Equity" value={`$${fmt(account.equity_usdt)}`} detail="Demo valuation" />
      <Metric label="Available" value={`$${fmt(account.available_usdt)}`} detail="USDT available" />
      <Metric label="Managed slots" value={`${fmt(positions.position_slots_in_use, 0)} / ${fmt(asRecord(data.settings).risk && asRecord(asRecord(data.settings).risk).max_open_positions, 0)}`} detail="Wallet assets excluded" />
      <Metric label="Pending plans" value={String(pending)} detail="TTL-bound" />
    </div>
    <div className="two-column">
      <Panel title="Trading eligibility" eyebrow={eligibility.eligible ? 'READY' : 'BLOCKED'}>
        <div className={`eligibility ${eligibility.eligible ? 'pass' : 'blocked'}`}><span>{eligibility.eligible ? '✓' : '!'}</span><div><b>{String(eligibility.reason ?? 'DATA_UNAVAILABLE')}</b><p>{JSON.stringify(eligibility.blocking_reasons ?? [])}</p></div></div>
      </Panel>
      <Panel title="Runtime posture" eyebrow="Independent safety domains">
        <dl className="definition-grid"><dt>Agent</dt><dd>{control?.agent_runtime_state ?? 'STOPPED'}</dd><dt>Mode</dt><dd>{control?.trading_mode ?? 'STOPPED'}</dd><dt>Execution</dt><dd>{control?.execution_state ?? 'DISARMED'}</dd><dt>Kill switch</dt><dd>{control?.kill_switch_active ? 'ACTIVE' : 'OFF'}</dd></dl>
      </Panel>
    </div>
    <Panel title="Account exposure" eyebrow="Wallet + agent-managed + reserved"><JsonView value={account.exposure} /></Panel>
  </>
}

function Metric({ label, value, detail }: { label: string; value: unknown; detail?: string }) {
  return <div className="metric"><small>{label}</small><strong>{String(value ?? '—')}</strong>{detail && <span>{detail}</span>}</div>
}

function Scanner({ data, busy, run }: { data: DashboardData; busy: string; run: () => void }) {
  const scans = Object.entries(data.scanner ?? {})
  return <>
    <Panel title="Configured symbol scanner" eyebrow="Core strategy output" actions={<Button onClick={run} disabled={Boolean(busy)}>Run read-only scan</Button>}>
      {scans.length ? <div className="scan-grid">{scans.map(([symbol, raw]) => { const item = asRecord(raw); return <article key={symbol} className="scan-card"><header><b>{symbol}</b><span className={`decision ${String(item.decision).toLowerCase()}`}>{String(item.decision ?? item.status ?? 'UNKNOWN')}</span></header><strong>{fmt(item.current_price, 8)}</strong><dl><dt>Signal score</dt><dd>{fmt(item.signal_score, 0)} / 10</dd><dt>Signal strength</dt><dd>{fmt(number(item.signal_strength) ? number(item.signal_strength)! * 100 : undefined)}%</dd><dt>Risk</dt><dd>{String(item.risk_status ?? item.reason ?? '—')}</dd></dl></article> })}</div> : <Empty text="Run a scan to populate market decisions" />}
    </Panel>
    <DataTable title="Signal history" rows={data.signals} />
  </>
}

function Approval({ plans, executionReady, busy, revalidate, reject }: { plans: Plan[]; executionReady: boolean; busy: string; revalidate: (plan: Plan) => void; reject: (plan: Plan) => void }) {
  return <Panel title="Server-owned TradePlans" eyebrow="Fresh revalidation required">
    {!plans.length ? <Empty text="No TradePlans recorded" /> : <div className="plan-list">{plans.map((plan) => {
      const pending = plan.ui_status === 'PENDING_APPROVAL'
      const seconds = Math.max(0, Math.floor((plan.expires_at_ms - Date.now()) / 1000))
      return <article className="plan-row" key={plan.plan_id}><div className="plan-symbol"><b>{plan.symbol}</b><code>{plan.plan_id}</code></div><div><small>Status</small><strong>{plan.ui_status ?? plan.status}</strong>{pending && <span>{seconds}s TTL</span>}</div><div><small>Score / strength</small><strong>{fmt(plan.signal_score, 0)} / {fmt(number(plan.signal_strength) ? number(plan.signal_strength)! * 100 : undefined)}%</strong></div><div><small>Entry / size</small><strong>{fmt(plan.entry, 8)} · {fmt(plan.position_size, 8)}</strong></div><div className="row-actions"><Button secondary disabled={!pending || Boolean(busy)} onClick={() => reject(plan)}>Reject</Button><Button disabled={!pending || !executionReady || Boolean(busy)} title={!executionReady ? 'Requires fresh WebSocket, running Agent and ARMED execution' : undefined} onClick={() => revalidate(plan)}>Revalidate & approve</Button></div></article>
    })}</div>}
  </Panel>
}

function DataTable({ title, rows }: { title: string; rows: Record<string, unknown>[] }) {
  const keys = useMemo(() => Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 8), [rows])
  return <Panel title={title} eyebrow={`${rows.length} records`}>{rows.length ? <div className="table-wrap"><table><thead><tr>{keys.map((key) => <th key={key}>{key.replaceAll('_', ' ')}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id ?? row.plan_id ?? index)}>{keys.map((key) => <td key={key}>{typeof row[key] === 'object' ? JSON.stringify(row[key]) : String(row[key] ?? '—')}</td>)}</tr>)}</tbody></table></div> : <Empty text="No records yet" />}</Panel>
}

function BacktestPanel({ result, symbol, days, busy, setSymbol, setDays, run }: { result: Record<string, unknown> | null; symbol: string; days: number; busy: string; setSymbol: (value: string) => void; setDays: (value: number) => void; run: (walk: boolean) => void }) {
  return <>
    <Panel title="Historical evaluation" eyebrow="Paginated OHLCV · production rules">
      <div className="form-row"><label>Symbol<input value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} /></label><label>Range<select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option></select></label><Button disabled={Boolean(busy)} onClick={() => run(false)}>Run backtest</Button><Button secondary disabled={Boolean(busy)} onClick={() => run(true)}>Walk-forward</Button></div>
    </Panel>
    <Panel title="Result" eyebrow="No strategy parameter tuning"><JsonView value={result} empty="Choose a configured range and run an evaluation" /></Panel>
  </>
}

function LogsPanel({ logs, audit }: { logs: string[]; audit: Record<string, unknown>[] }) {
  return <div className="two-column"><Panel title="Redacted runtime log" eyebrow={`${logs.length} lines`}><div className="log-view">{logs.length ? logs.map((line, index) => <code key={index}>{line}</code>) : <Empty text="No runtime logs" />}</div></Panel><Panel title="Control transition audit" eyebrow={`${audit.length} events`}><div className="audit-list">{audit.length ? audit.map((item, index) => <article key={String(item.id ?? index)}><small>{stamp(item.timestamp_ms)}</small><b>{String(item.requested_action)}</b><span>{String(item.reason)}</span></article>) : <Empty text="No control transitions" />}</div></Panel></div>
}

function SettingsPanel({ data, control, stream, highRiskReady, busy, setMode, act, refresh }: { data: DashboardData; control?: ControlState; stream: Connection; highRiskReady: boolean; busy: string; setMode: (mode: TradingMode) => void; act: (name: string, callback: () => Promise<unknown>) => Promise<void>; refresh: () => Promise<void> }) {
  const [interval, setIntervalValue] = useState(control?.scan_interval_seconds ?? 15)
  const [autoText, setAutoText] = useState('')
  const [resetText, setResetText] = useState('')
  useEffect(() => setIntervalValue(control?.scan_interval_seconds ?? 15), [control?.scan_interval_seconds])
  return <>
    <Panel title="Runtime controls" eyebrow="Action API · audited transitions">
      <div className="control-stack">
        <div className="control-line"><div><small>Environment</small><b>DEMO</b><p>Live is not configured in W0–W7.</p></div><button className="segmented active">DEMO</button><button className="segmented" disabled>LIVE · LOCKED</button></div>
        <div className="control-line"><div><small>Trading mode</small><b>{control?.trading_mode ?? 'STOPPED'}</b><p>Mode and execution arming are independent.</p></div>{(['STOPPED', 'DRY_RUN', 'MANUAL_APPROVAL', 'AUTO'] as TradingMode[]).map((mode) => <button key={mode} className={`segmented ${control?.trading_mode === mode ? 'active' : ''}`} disabled={Boolean(busy) || (mode === 'AUTO' && (!control?.auto_demo_enabled || stream !== 'CONNECTED'))} onClick={() => setMode(mode)}>{mode}</button>)}</div>
        <div className="control-line"><div><small>Agent runtime</small><b>{control?.agent_runtime_state ?? 'STOPPED'}</b><p>Scanner and reconciliation use the authoritative worker.</p></div><Button disabled={Boolean(busy) || control?.trading_mode === 'STOPPED'} onClick={() => void act('Start agent', () => write('/agent/start'))}>Start</Button><Button secondary disabled={Boolean(busy)} onClick={() => void act('Stop agent', () => write('/agent/stop'))}>Stop</Button></div>
        <div className="control-line"><div><small>Demo execution</small><b>{control?.execution_state ?? 'DISARMED'}</b><p>ARM never survives a backend restart.</p></div><Button disabled={!highRiskReady || Boolean(busy) || control?.agent_runtime_state !== 'RUNNING' || control?.trading_mode === 'DRY_RUN' || control?.trading_mode === 'STOPPED'} onClick={() => void act('Arm execution', () => write('/execution/arm'))}>ARM</Button><Button secondary disabled={Boolean(busy)} onClick={() => void act('Disarm execution', () => write('/execution/disarm'))}>DISARM</Button></div>
      </div>
    </Panel>
    <div className="two-column">
      <Panel title="AUTO DEMO" eyebrow="Default off · session-only"><p className="panel-copy">AUTO can only be selected after an explicit enable phrase. It still uses Core signal, risk, sizing, plan and approval revalidation.</p><label className="confirmation-field">Type <code>ENABLE AUTO DEMO</code><input value={autoText} onChange={(event) => setAutoText(event.target.value)} /></label><div className="button-row"><Button disabled={autoText !== 'ENABLE AUTO DEMO' || !highRiskReady || Boolean(busy)} onClick={() => void act('Enable AUTO DEMO', () => write('/auto-demo/enable', { confirmation: autoText }))}>Enable</Button><Button secondary disabled={Boolean(busy)} onClick={() => void act('Disable AUTO DEMO', () => write('/auto-demo/disable'))}>Disable</Button></div></Panel>
      <Panel title="Kill switch" eyebrow={control?.kill_switch_active ? 'ACTIVE' : 'OFF'}><p className="panel-copy">Stops new entries, AUTO and the runtime. Existing protective TP/SL orders are preserved.</p><div className="button-row"><Button danger disabled={Boolean(busy)} onClick={() => { if (window.confirm('Activate kill switch? Existing protection remains active.')) void act('Kill switch', () => write('/kill-switch')) }}>ACTIVATE KILL SWITCH</Button></div>{control?.kill_switch_active && <><label className="confirmation-field">Type <code>RESET KILL SWITCH</code><input value={resetText} onChange={(event) => setResetText(event.target.value)} /></label><Button secondary disabled={resetText !== 'RESET KILL SWITCH' || Boolean(busy)} onClick={() => void act('Reset kill switch', () => write('/kill-switch/reset', { confirmation: resetText }))}>Reset to safe STOPPED</Button></>}</Panel>
    </div>
    <Panel title="Runtime settings" eyebrow="Low-risk allowlist only"><div className="form-row"><label>Scan interval (seconds)<input type="number" min={5} max={3600} value={interval} onChange={(event) => setIntervalValue(Number(event.target.value))} /></label><Button secondary disabled={Boolean(busy)} onClick={() => void act('Update interval', () => write('/settings/runtime', { scan_interval_seconds: interval }, 'PUT'))}>Save interval</Button></div><JsonView value={data.settings} /></Panel>
  </>
}

export default App
