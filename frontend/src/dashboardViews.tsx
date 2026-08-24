import { useMemo, useState, type ReactNode } from 'react'
import {
  formatAge,
  formatDuration,
  formatIdentifier,
  formatNumber,
  formatPercent,
  formatPnL,
  formatPrice,
  formatQuantity,
  formatTime,
  formatTimestamp,
  formatUSDT,
  numeric,
} from './formatters'
import { fieldLabel, t, translateCode, type Language } from './locales'
import type { ControlState, DashboardData, Plan, SessionState } from './types'

export const copy = (language: Language, zh: string, en: string) => language === 'zh' ? zh : en

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord) : []
}

export function marketStreamState(market: Record<string, unknown>): string {
  const states = records(Object.values(asRecord(market.symbols)))
    .map((item) => String(item.stream_state ?? 'DISCONNECTED'))
  if (states.length && states.every((item) => item === 'CONNECTED')) return 'CONNECTED'
  if (states.some((item) => item === 'RESYNCING')) return 'RESYNCING'
  if (states.some((item) => item === 'STALE')) return 'STALE'
  return 'DISCONNECTED'
}

export function Panel({
  title, eyebrow, children, actions, className = '',
}: {
  title: string
  eyebrow?: string
  children: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return <section className={`panel ${className}`}>
    <header className="panel-header"><div>{eyebrow && <small>{eyebrow}</small>}<h2>{title}</h2></div>{actions}</header>
    {children}
  </section>
}

export function Empty({ text }: { text: string }) {
  return <div className="empty"><span className="empty-orb">—</span><p>{text}</p></div>
}

export function Metric({
  label, value, detail, accent, valueClassName = '',
}: {
  label: string
  value: ReactNode
  detail?: string
  accent?: 'blue' | 'amber'
  valueClassName?: string
}) {
  return <article className={`metric ${accent ?? ''}`}><small>{label}</small><strong className={valueClassName}>{value}</strong>{detail && <span>{detail}</span>}</article>
}

export function StateTag({ value, language, tone }: { value: unknown; language: Language; tone?: string }) {
  const code = String(value ?? 'UNKNOWN')
  const danger = ['POSITION_UNPROTECTED', 'RECONCILIATION_FAILED', 'PROTECTION_CLEANUP_INCOMPLETE', 'FLATTEN_INCOMPLETE'].includes(code)
  const warning = ['CANCEL_REQUESTED', 'SUBMISSION_UNKNOWN', 'RECONCILIATION_REQUIRED'].includes(code)
  const success = ['FILLED', 'CLOSED', 'PROTECTED', 'FLAT'].includes(code)
  const resolvedTone = tone ?? (danger ? 'danger' : warning ? 'warning' : success ? 'success' : code === 'CANCELLED' ? 'muted' : 'info')
  return <span className={`state-tag ${resolvedTone}`}>{translateCode(language, code)}</span>
}

function displayPrimitive(value: unknown, language: Language): string {
  if (value === undefined || value === null) return '—'
  if (typeof value === 'boolean') return translateCode(language, value)
  if (typeof value === 'number') return formatNumber(value, language, 8, 0)
  return translateCode(language, value)
}

export function ObjectView({ value, language, empty }: { value: unknown; language: Language; empty: string }) {
  if (value === undefined || value === null || value === 'DATA_UNAVAILABLE') return <Empty text={empty} />
  if (Array.isArray(value)) {
    if (!value.length) return <Empty text={empty} />
    return <div className="object-list">{value.map((item, index) => <ObjectView key={index} value={item} language={language} empty={empty} />)}</div>
  }
  const record = asRecord(value)
  if (!Object.keys(record).length) return <span className="primitive-value">{displayPrimitive(value, language)}</span>
  return <dl className="object-grid">{Object.entries(record).map(([key, item]) => <div key={key}>
    <dt>{fieldLabel(language, key)}</dt>
    <dd>{item && typeof item === 'object'
      ? <ObjectView value={item} language={language} empty={empty} />
      : displayPrimitive(item, language)}</dd>
  </div>)}</dl>
}

export function activeAgentEntries(orders: unknown): number {
  const lifecycle = records(asRecord(orders).agent_order_lifecycle)
  const active = new Set(['PLANNED', 'APPROVED', 'SUBMITTED', 'SUBMISSION_UNKNOWN', 'OPEN', 'PARTIALLY_FILLED', 'CANCEL_REQUESTED'])
  return lifecycle.filter((item) => active.has(String(item.state))).length
}

export function flattenEnabled(session: SessionState | string, managedCount: number, activeEntries: number): boolean {
  if (session === 'FLATTENING') return false
  if (session === 'STOPPED') return managedCount > 0
  return managedCount > 0 || activeEntries > 0
}

export function SessionControls({
  state, writesReady, managedCount, activeEntries, busy, onStart, onPause, onStop, onFlatten, language,
}: {
  state: SessionState
  writesReady: boolean
  managedCount: number
  activeEntries: number
  busy: boolean
  onStart: () => void
  onPause: () => void
  onStop: () => void
  onFlatten: () => void
  language: Language
}) {
  const disabled = busy || state === 'FLATTENING'
  return <div className="session-actions" role="group" aria-label={copy(language, '自动交易控制', 'Automatic trading controls')}>
    {state === 'STOPPED' && <button className="button start-button" disabled={!writesReady || disabled} onClick={onStart}>{copy(language, '启动自动交易', 'Start auto trading')}</button>}
    {state === 'PAUSED' && <button className="button start-button" disabled={!writesReady || disabled} onClick={onStart}>{copy(language, '继续自动交易', 'Resume auto trading')}</button>}
    {state === 'RUNNING' && <button className="button secondary" disabled={disabled} onClick={onPause}>{copy(language, '暂停', 'Pause')}</button>}
    {state !== 'STOPPED' && state !== 'FLATTENING' && <button className="button secondary" disabled={busy} onClick={onStop}>{copy(language, '停止', 'Stop')}</button>}
    <button className="button danger" disabled={!writesReady || disabled || !flattenEnabled(state, managedCount, activeEntries)} onClick={onFlatten}>{state === 'FLATTENING' ? copy(language, '正在全部平仓…', 'Flattening…') : copy(language, '全部平仓并停止', 'Flatten all & stop')}</button>
  </div>
}

function sessionDescription(state: SessionState, language: Language): string {
  const content: Record<SessionState, [string, string]> = {
    STOPPED: ['当前不会建立新仓位；账户核对与已有仓位保护仍继续。', 'No new positions will open; reconciliation and existing protection continue.'],
    RUNNING: ['自动交易运行中；每笔交易仍经过策略、风控、仓位计算与执行复核。', 'Automatic trading is running; every trade still passes strategy, risk, sizing, and execution revalidation.'],
    PAUSED: ['自动交易已暂停；新开仓已阻止，现有仓位、止盈止损与核对继续运行。', 'Automatic trading is paused; new entries are blocked while positions, protection, and reconciliation continue.'],
    FLATTENING: ['正在阻止新开仓、取消代理待成交入场单、关闭托管仓位并清理保护单。', 'Blocking entries, cancelling Agent entry orders, closing managed positions, and cleaning protection.'],
    DEGRADED: ['会话已安全降级；新开仓已阻止，请查看具体原因。', 'The session failed closed; new entries are blocked. Review the reason below.'],
  }
  return copy(language, ...content[state])
}

export function SessionControlCard({
  state, session, market, protection, managedCount, activeEntries, writesReady, busy,
  onStart, onPause, onStop, onFlatten, language, compact = false,
}: {
  state: SessionState
  session: Record<string, unknown>
  market: Record<string, unknown>
  protection: { protected: number; total: number; critical: boolean }
  managedCount: number
  activeEntries: number
  writesReady: boolean
  busy: boolean
  onStart: () => void
  onPause: () => void
  onStop: () => void
  onFlatten: () => void
  language: Language
  compact?: boolean
}) {
  const preflight = asRecord(session.preflight)
  const blockers = Array.isArray(preflight.blockers) ? preflight.blockers : []
  const reason = session.degraded_reason ?? (state === 'DEGRADED' ? blockers[0] : undefined)
  const flattenIncomplete = session.status === 'FLATTEN_INCOMPLETE'
  const remainingPositions = records(session.remaining_positions)
  const unresolvedEntries = records(session.unresolved_entries)
  const marketState = marketStreamState(market)
  return <Panel title={copy(language, '自动交易会话', 'Auto Session')} eyebrow={translateCode(language, state)} className={`session-panel session-${state.toLowerCase()} ${compact ? 'session-compact' : ''}`}>
    <div className="session-summary"><div><StateTag value={state} language={language} /><p className="panel-copy">{sessionDescription(state, language)}</p></div>
      <dl className="session-facts">
        <div><dt>{copy(language, '市场数据流', 'Market stream')}</dt><dd><StateTag value={marketState} language={language} /></dd></div>
        <div><dt>{copy(language, '风控资格', 'Risk eligibility')}</dt><dd>{preflight.passed ? translateCode(language, 'PASS') : translateCode(language, blockers[0] ?? 'NOT_RUN')}</dd></div>
        <div><dt>{copy(language, '托管仓位', 'Managed positions')}</dt><dd>{managedCount}</dd></div>
        <div><dt>{copy(language, '保护状态', 'Protection')}</dt><dd className={protection.critical ? 'critical-text' : ''}>{protection.protected} / {protection.total}{protection.critical ? ` ${translateCode(language, 'POSITION_UNPROTECTED')}` : ''}</dd></div>
      </dl>
    </div>
    {state === 'DEGRADED' && <div className="critical-alert"><b>{copy(language, '会话安全降级 · 禁止新开仓', 'Session degraded · New entries blocked')}</b><span>{translateCode(language, reason ?? 'DATA_UNAVAILABLE')}</span></div>}
    {flattenIncomplete && <div className="critical-alert"><b>{translateCode(language, 'FLATTEN_INCOMPLETE')}</b><span>{copy(language, `仍有 ${remainingPositions.length} 个仓位、${unresolvedEntries.length} 个订单需要继续核对。`, `${remainingPositions.length} positions and ${unresolvedEntries.length} orders still require reconciliation.`)}</span>{remainingPositions.map((item, index) => <span key={String(item.plan_id ?? index)}>{String(item.symbol ?? '—')} · {formatQuantity(item.quantity, language)}</span>)}</div>}
    {state === 'FLATTENING' && <ol className="flatten-progress"><li>{copy(language, '正在阻止新开仓', 'Blocking new entries')}</li><li>{copy(language, '正在取消代理待成交入场单', 'Cancelling Agent entry orders')}</li><li>{copy(language, '正在关闭托管仓位', 'Closing managed positions')}</li><li>{copy(language, '正在确认成交并清理保护单', 'Confirming fills and cleaning protection')}</li></ol>}
    <SessionControls state={state} writesReady={writesReady} managedCount={managedCount} activeEntries={activeEntries} busy={busy} onStart={onStart} onPause={onPause} onStop={onStop} onFlatten={onFlatten} language={language} />
  </Panel>
}

export function StopDialog({ language, onCancel, onConfirm }: { language: Language; onCancel: () => void; onConfirm: () => void }) {
  return <div className="modal-backdrop" role="presentation"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="stop-title">
    <header><div><small>{copy(language, '自动交易会话', 'AUTO SESSION')}</small><h2 id="stop-title">{copy(language, '停止自动交易？', 'Stop automatic trading?')}</h2></div></header>
    <div className="confirmation-copy"><p>{copy(language, '停止后将禁止新开仓并取消代理待成交入场单。', 'Stopping blocks new entries and cancels pending Agent entry orders.')}</p><p><b>{copy(language, '已有托管仓位不会被强制平仓，止盈止损保护继续保留。', 'Existing managed positions will not be force-closed; stop-loss and take-profit protection remain active.')}</b></p></div>
    <div className="modal-actions"><button className="button secondary" onClick={onCancel}>{copy(language, '取消', 'Cancel')}</button><button className="button" onClick={onConfirm}>{copy(language, '停止自动交易', 'Stop automatic trading')}</button></div>
  </section></div>
}

export function FlattenDialog({
  positions, externalCount, language, onCancel, onConfirm,
}: {
  positions: Record<string, unknown>[]
  externalCount: number
  language: Language
  onCancel: () => void
  onConfirm: () => void
}) {
  return <div className="modal-backdrop" role="presentation"><section className="modal danger-modal" role="dialog" aria-modal="true" aria-labelledby="flatten-title">
    <header><div><small>{copy(language, '高风险操作', 'High-risk action')}</small><h2 id="flatten-title">{copy(language, '全部平仓并停止', 'Flatten all & stop')}</h2></div></header>
    {positions.length ? <div className="flatten-position-list">{positions.map((position, index) => <article key={String(position.plan_id ?? index)}><b>{String(position.symbol ?? '—')}</b><span>{formatQuantity(numeric(position.quantity) === undefined ? undefined : Math.max(0, Number(position.quantity) - Number(position.exit_filled_quantity ?? 0)), language)} {String(position.symbol ?? '').split('-')[0]}</span><StateTag value="AGENT" language={language} /></article>)}</div> : <p className="empty-inline">{copy(language, '当前没有自动交易代理托管仓位需要平仓。', 'There are no Agent-managed positions to flatten.')}</p>}
    <div className="external-safe-note"><b>{copy(language, '外部钱包资产不会被处理', 'External wallet assets will not be touched')}</b><span>{copy(language, `包括 ${externalCount} 项手机或手动持有资产，系统不会自动出售。`, `Including ${externalCount} mobile/manual wallet assets; they will not be sold.`)}</span></div>
    <div className="modal-actions"><button className="button secondary" onClick={onCancel}>{copy(language, '取消', 'Cancel')}</button><button className="button danger" disabled={!positions.length} onClick={onConfirm}>{copy(language, '确认全部平仓并停止', 'Confirm flatten all & stop')}</button></div>
  </section></div>
}

export function protectionSummary(positions: unknown): { protected: number; total: number; critical: boolean } {
  const managed = records(positions)
  const protectedCount = managed.filter((item) => item.protection_state === 'PROTECTED').length
  return { protected: protectedCount, total: managed.length, critical: managed.some((item) => item.protection_state !== 'PROTECTED') }
}

export function managedUnrealizedPnL(positions: unknown, market: unknown): number | undefined {
  const managed = records(positions)
  if (!managed.length) return 0
  const symbols = asRecord(asRecord(market).symbols)
  let total = 0
  for (const position of managed) {
    const symbol = String(position.symbol ?? '')
    const realtime = asRecord(symbols[symbol])
    const entry = numeric(position.entry_price)
    const quantity = numeric(position.quantity)
    const exited = numeric(position.exit_filled_quantity) ?? 0
    const current = numeric(realtime.last_price)
    if (
      !symbol
      || realtime.stream_state !== 'CONNECTED'
      || entry === undefined
      || quantity === undefined
      || current === undefined
    ) return undefined
    total += (current - entry) * Math.max(0, quantity - exited)
  }
  return total
}

export function OverviewMetrics({ data, language }: { data: DashboardData; language: Language }) {
  const account = asRecord(data.account)
  const positions = asRecord(data.positions)
  const exposure = asRecord(account.exposure ?? positions.account_exposure)
  const summary = asRecord(positions.exposure_summary)
  const status = asRecord(data.status)
  const observability = asRecord(status.observability)
  const market = asRecord(data.market ?? status.market)
  const marketMetrics = asRecord(market.metrics)
  const managedCount = numeric(positions.managed_open_positions) ?? 0
  const reserved = numeric(positions.reserved_entry_notional)
  const protection = protectionSummary(positions.managed_positions)
  const walletPct = numeric(observability.wallet_exposure_pct)
  const managedExposure = numeric(summary.managed_exposure_usdt ?? exposure.managed_exposure_usdt)
  const managedExposurePct = numeric(summary.managed_exposure_pct ?? exposure.managed_exposure_pct)
  const unrealizedPnL = managedUnrealizedPnL(positions.managed_positions, market)
  const confirmed = numeric(marketMetrics.confirmed_candle_timestamp)
    ?? Math.max(0, ...records(Object.values(asRecord(market.symbols))).map((item) => numeric(asRecord(item.confirmed_candle_timestamps)['1m']) ?? 0))
  return <>
    <div className="metric-row overview-primary">
      <Metric label={copy(language, '账户权益', 'Account equity')} value={formatUSDT(account.equity_usdt, language)} detail={copy(language, 'OKX 模拟盘账户总权益', 'OKX Demo total account equity')} accent="blue" />
      <Metric label={copy(language, '可用 USDT', 'Available USDT')} value={formatUSDT(account.available_usdt, language)} detail={copy(language, '可用于交易的 USDT 余额', 'USDT balance available to trade')} />
      <Metric label={copy(language, '未实现损益', 'Unrealized PnL')} value={formatPnL(unrealizedPnL, language)} detail={copy(language, '当前托管仓位浮动盈亏', 'Open Agent-managed position PnL')} valueClassName={(unrealizedPnL ?? 0) > 0 ? 'positive' : (unrealizedPnL ?? 0) < 0 ? 'negative' : ''} />
      <Metric label={copy(language, '托管仓位', 'Managed positions')} value={`${formatNumber(managedCount, language, 0, 0)} / ${formatNumber(asRecord(asRecord(data.settings).risk).max_open_positions, language, 0, 0)}`} detail={copy(language, '不包含外部钱包资产', 'External wallet assets excluded')} />
      <Metric label={copy(language, '保护状态', 'Protection')} value={`${protection.protected} / ${protection.total}`} detail={protection.critical ? translateCode(language, 'POSITION_UNPROTECTED') : copy(language, '止盈止损保护', 'Stop-loss / take-profit protection')} accent={protection.critical ? 'amber' : undefined} />
    </div>
    <div className="metric-row compact">
      <Metric label={copy(language, '市场数据流', 'Market stream')} value={<StateTag value={marketStreamState(market)} language={language} />} />
      <Metric label={copy(language, '最近确认的 1 分钟 K 线', 'Latest confirmed 1m candle')} value={confirmed > 0 ? formatTime(confirmed, language) : '—'} />
      <Metric label={copy(language, '行情数据延迟', 'Market data age')} value={formatAge(marketMetrics.market_latency_current_ms === undefined ? undefined : Number(marketMetrics.market_latency_current_ms) / 1000, language)} />
      <Metric label={copy(language, '账户同步延迟', 'Account sync age')} value={formatAge(observability.account_freshness_age_seconds, language)} />
      <Metric label={fieldLabel(language, 'wallet_exposure_pct')} value={walletPct === undefined ? t(language, 'dataUnavailable') : formatPercent(walletPct, language)} />
      <Metric label={fieldLabel(language, 'managed_exposure_usdt')} value={managedExposure === undefined ? t(language, 'dataUnavailable') : formatUSDT(managedExposure, language)} />
      <Metric label={fieldLabel(language, 'managed_exposure_pct')} value={managedExposurePct === undefined ? t(language, 'dataUnavailable') : formatPercent(managedExposurePct, language)} />
      <Metric label={fieldLabel(language, 'reserved_entry_notional')} value={reserved === undefined ? t(language, 'dataUnavailable') : formatUSDT(reserved, language)} />
      <Metric label={fieldLabel(language, 'daily_pnl')} value={formatPnL(observability.daily_pnl ?? account.daily_pnl, language)} valueClassName={(numeric(observability.daily_pnl ?? account.daily_pnl) ?? 0) > 0 ? 'positive' : (numeric(observability.daily_pnl ?? account.daily_pnl) ?? 0) < 0 ? 'negative' : ''} />
    </div>
  </>
}

function latestSignal(signals: Record<string, unknown>[], symbol: string): Record<string, unknown> {
  return signals.find((item) => item.symbol === symbol) ?? {}
}

function primaryReason(item: Record<string, unknown>): unknown {
  if (item.risk_status && item.risk_status !== 'PASS') return item.risk_status
  if (item.reason) return item.reason
  if (item.decision === 'HOLD') return 'SIGNAL_HOLD'
  if (item.decision === 'REJECT' || item.decision === 'REJECTED') return 'RISK_REASON_UNAVAILABLE'
  return Array.isArray(item.reasons) ? item.reasons[0] : undefined
}

export function MarketSignals({ data, language }: { data: DashboardData; language: Language }) {
  const market = asRecord(data.market ?? data.status?.market)
  const marketSymbols = asRecord(market.symbols)
  const scanner = asRecord(data.scanner)
  const configured = Array.isArray(asRecord(data.settings).symbols) ? asRecord(data.settings).symbols as string[] : ['BTC-USDT', 'ETH-USDT', 'SOL-USDT']
  return <>
    <Panel title={copy(language, '实时市场状态', 'Realtime Market Status')} eyebrow={copy(language, '由已确认的 1 分钟 K 线触发策略', 'Confirmed 1m candle strategy trigger')}>
      <div className="scan-grid">{configured.map((symbol) => {
        const realtime = asRecord(marketSymbols[symbol])
        const scan = asRecord(scanner[symbol])
        const signal = Object.keys(scan).length ? scan : latestSignal(data.signals, symbol)
        const decision = signal.decision ?? (realtime.last_price === undefined ? 'DATA_UNAVAILABLE' : 'WAITING')
        const candles = asRecord(realtime.confirmed_candle_timestamps)
        return <article className="scan-card" key={symbol}>
          <header><b>{symbol}</b><StateTag value={decision} language={language} /></header>
          <strong>{formatPrice(realtime.last_price ?? signal.current_price, language)}</strong>
          <dl>
            <dt>{copy(language, '数据流', 'Stream')}</dt><dd>{translateCode(language, realtime.stream_state ?? 'DISCONNECTED')}</dd>
            <dt>{copy(language, '信号评分', 'Signal score')}</dt><dd>{numeric(signal.score ?? signal.signal_score) === undefined ? '—' : `${formatNumber(signal.score ?? signal.signal_score, language, 0, 0)} / 10`}</dd>
            <dt>{copy(language, '信号强度', 'Signal strength')}</dt><dd>{formatPercent(signal.signal_strength, language)}</dd>
            <dt>{copy(language, '1 分钟', '1m')}</dt><dd>{formatTime(candles['1m'], language)}</dd>
            <dt>{copy(language, '3 分钟', '3m')}</dt><dd>{formatTime(candles['3m'], language)}</dd>
            <dt>{copy(language, '5 分钟', '5m')}</dt><dd>{formatTime(candles['5m'], language)}</dd>
            <dt>{copy(language, '最近策略评估', 'Last strategy evaluation')}</dt><dd>{formatTimestamp(signal.timestamp_ms ?? signal.created_at_ms, language)}</dd>
            <dt>{copy(language, '主要原因', 'Primary reason')}</dt><dd>{translateCode(language, primaryReason(signal) ?? 'NOT_AVAILABLE')}</dd>
          </dl>
        </article>
      })}</div>
    </Panel>
    <SignalHistory rows={data.signals} language={language} />
  </>
}

export function SignalHistory({ rows, language }: { rows: Record<string, unknown>[]; language: Language }) {
  return <Panel title={copy(language, '信号历史', 'Signal History')} eyebrow={`${rows.length} ${t(language, 'records')}`}>
    {!rows.length ? <Empty text={t(language, 'noRecords')} /> : <div className="table-wrap"><table><thead><tr>
      <th>{copy(language, '时间', 'Time')}</th><th>{copy(language, '交易对', 'Symbol')}</th><th>{copy(language, '评分', 'Score')}</th><th>{copy(language, '信号强度', 'Strength')}</th><th>{copy(language, '决策', 'Decision')}</th><th>{copy(language, '主要原因', 'Primary reason')}</th><th>{copy(language, '详情', 'Details')}</th>
    </tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id ?? `${row.symbol}-${row.timestamp_ms}-${index}`)}>
      <td title={formatTimestamp(row.timestamp_ms, language)}>{formatTimestamp(row.timestamp_ms, language)}</td>
      <td><b>{String(row.symbol ?? '—')}</b></td><td>{formatNumber(row.score ?? row.signal_score, language, 0, 0)}</td><td>{formatPercent(row.signal_strength, language)}</td><td><StateTag value={row.decision ?? 'UNKNOWN'} language={language} /></td><td>{translateCode(language, primaryReason(row) ?? '—')}</td>
      <td><details className="row-details"><summary>{copy(language, '查看', 'View')}</summary><ReasonList reasons={row.reasons} language={language} /><details><summary>{copy(language, '原始数据 / 开发者详情', 'Raw / Developer Details')}</summary><code>{String(row.reasons_json ?? '—')}</code></details></details></td>
    </tr>)}</tbody></table></div>}
  </Panel>
}

export function ReasonList({ reasons, language }: { reasons: unknown; language: Language }) {
  const items = Array.isArray(reasons) ? reasons : []
  if (!items.length) return <span>—</span>
  return <ul className="reason-list">{items.map((reason, index) => <li key={index}><span>✓</span>{translateCode(language, reason)}</li>)}</ul>
}

export function ApprovalCenter({
  plans, sessionState, executionReady, busy, onPreview, onReject, language,
}: {
  plans: Plan[]
  sessionState: SessionState
  executionReady: boolean
  busy: boolean
  onPreview: (plan: Plan) => void
  onReject: (plan: Plan) => void
  language: Language
}) {
  return <Panel title={copy(language, '服务端交易计划', 'Server-owned TradePlans')} eyebrow={copy(language, '人工 / 诊断审批兼容', 'Manual / diagnostic approval compatibility')}>
    <div className="info-banner"><b>{copy(language, '自动交易会话运行时无需逐笔审批', 'Per-trade approval is not required while AUTO Session runs')}</b><span>{copy(language, '本页继续用于人工操作、诊断与检查；审批仍会触发预览、挑战校验与实时复核。', 'This page remains available for manual use, diagnostics, and inspection; approval still uses Preview, Challenge, and fresh revalidation.')}</span></div>
    {!plans.length ? <Empty text={t(language, 'noPlans')} /> : <div className="plan-list">{plans.map((plan) => {
      const item = plan as Plan & Record<string, unknown>
      const pending = plan.ui_status === 'PENDING_APPROVAL'
      return <article className="plan-row plan-row-expanded" key={plan.plan_id}>
        <div className="plan-symbol"><b>{plan.symbol}</b><code title={plan.plan_id}>{formatIdentifier(plan.plan_id)}</code></div>
        <div><small>{copy(language, '方向 / 状态', 'Direction / status')}</small><strong>{translateCode(language, item.side ?? plan.decision ?? 'UNKNOWN')} · {translateCode(language, plan.ui_status ?? plan.status)}</strong></div>
        <div><small>{copy(language, '当前价 / 可执行价', 'Current / executable')}</small><strong>{formatPrice(item.current_price, language)} / {formatPrice(plan.entry, language)}</strong></div>
        <div><small>{copy(language, '止损价 / 止盈价', 'SL / TP')}</small><strong>{formatPrice(plan.stop, language)} / {formatPrice(plan.take_profit, language)}</strong></div>
        <div><small>{copy(language, '仓位 / 风险 / 盈亏比', 'Size / risk / RR')}</small><strong>{formatQuantity(plan.position_size, language)} · {formatPercent(item.risk_pct, language)} · {formatNumber(item.risk_reward, language, 2, 2)}</strong></div>
        <div><small>{copy(language, '评分 / 创建时间', 'Score / created')}</small><strong>{formatNumber(plan.signal_score, language, 0, 0)} / 10 · {formatTimestamp(item.created_at_ms, language)}</strong></div>
        <div className="row-actions"><button className="button secondary" disabled={!pending || busy} onClick={() => onReject(plan)}>{copy(language, '拒绝', 'Reject')}</button><button className="button" disabled={!pending || !executionReady || busy || sessionState === 'RUNNING'} onClick={() => onPreview(plan)}>{copy(language, '预览并审批', 'Preview & approve')}</button></div>
        <details className="advanced-details plan-details"><summary>{copy(language, '完整计划详情', 'Full plan details')}</summary><ObjectView value={plan} language={language} empty="—" /></details>
      </article>
    })}</div>}
  </Panel>
}

const normalLifecycle = ['PLANNED', 'APPROVED', 'SUBMITTED', 'OPEN', 'PARTIALLY_FILLED', 'FILLED', 'PROTECTED', 'CLOSED']

function LifecycleRail({ state, protection, language }: { state: string; protection: string; language: Language }) {
  const effective = state === 'FILLED' && protection === 'PROTECTED' ? 'PROTECTED' : state
  const current = normalLifecycle.indexOf(effective)
  return <div className="lifecycle-rail" role="img" aria-label={`${translateCode(language, state)} / ${translateCode(language, protection)}`}>{normalLifecycle.map((stage, index) => <span key={stage} className={current >= 0 && index <= current ? 'done' : ''} title={translateCode(language, stage)} />)}</div>
}

export function OrderLifecycle({ value, language }: { value: Record<string, unknown> | undefined; language: Language }) {
  const data = asRecord(value)
  const lifecycle = records(data.agent_order_lifecycle)
  const exchange = records(data.okx_open_orders)
  const all: Record<string, unknown>[] = [
    ...lifecycle.map((item): Record<string, unknown> => ({ ...item, origin: item.origin ?? 'AGENT' })),
    ...exchange.filter((item) => item.origin !== 'AGENT'),
  ]
  return <Panel title={copy(language, '订单生命周期', 'Order Lifecycle')} eyebrow={copy(language, '持久化状态 · OKX 核对', 'Persistent state · OKX reconciliation')}>
    {!all.length ? <Empty text={t(language, 'noOrders')} /> : <div className="order-card-list">{all.map((row, index) => {
      const state = String(row.state ?? row.status ?? 'OPEN')
      const protection = String(row.protection_state ?? 'NOT_APPLICABLE')
      return <article className={`order-card ${['POSITION_UNPROTECTED', 'RECONCILIATION_FAILED'].includes(state) ? 'critical-card' : ''}`} key={String(row.plan_id ?? row.order_id ?? index)}>
        <header><div><b>{String(row.symbol ?? '—')}</b><span title={String(row.plan_id ?? row.order_id ?? '')}>{formatIdentifier(row.plan_id ?? row.order_id)}</span></div><StateTag value={row.origin ?? 'EXTERNAL'} language={language} /></header>
        <div className="order-state-line"><StateTag value={state} language={language} /><LifecycleRail state={state} protection={protection} language={language} /><StateTag value={protection} language={language} /></div>
        <dl><dt>{copy(language, '请求数量', 'Requested')}</dt><dd>{formatQuantity(row.requested_size ?? row.size, language)}</dd><dt>{copy(language, '已成交', 'Filled')}</dt><dd>{formatQuantity(row.filled_size, language)}</dd><dt>{copy(language, '更新时间', 'Updated')}</dt><dd>{formatTimestamp(row.updated_at_ms ?? row.timestamp_ms, language)}</dd></dl>
        <details className="advanced-details"><summary>{copy(language, '订单详情', 'Order details')}</summary><ObjectView value={row} language={language} empty="—" /></details>
      </article>
    })}</div>}
  </Panel>
}

export function filterDisplayDust(inventory: Record<string, unknown>[], hideDust: boolean): Record<string, unknown>[] {
  return hideDust ? inventory.filter((item) => item.display_dust !== true) : inventory
}

export function PositionsView({ value, settings, language }: { value: Record<string, unknown> | undefined; settings: Record<string, unknown> | undefined; language: Language }) {
  const data = asRecord(value)
  const managed = records(data.managed_positions)
  const external = records(data.external_wallet_inventory)
  const summary = asRecord(data.exposure_summary)
  const [hideDust, setHideDust] = useState(true)
  const visibleExternal = filterDisplayDust(external, hideDust)
  const hiddenCount = external.length - visibleExternal.length
  const maxPositions = numeric(asRecord(asRecord(settings).risk).max_open_positions)
  const protection = protectionSummary(managed)
  return <>
    <div className="metric-row">
      <Metric label={copy(language, '托管仓位数', 'Managed positions')} value={`${formatNumber(data.managed_open_positions, language, 0, 0)} / ${formatNumber(maxPositions, language, 0, 0)}`} detail={copy(language, '自动交易代理管理的仓位优先显示', 'Agent-managed positions shown first')} accent="blue" />
      <Metric label={copy(language, '托管仓位敞口', 'Managed exposure')} value={numeric(summary.managed_exposure_usdt) === undefined ? t(language, 'dataUnavailable') : formatUSDT(summary.managed_exposure_usdt, language)} />
      <Metric label={copy(language, '托管敞口比例', 'Managed exposure ratio')} value={numeric(summary.managed_exposure_pct) === undefined ? t(language, 'dataUnavailable') : formatPercent(summary.managed_exposure_pct, language)} />
      <Metric label={copy(language, '已预留入场名义金额', 'Reserved entry notional')} value={formatUSDT(data.reserved_entry_notional, language)} detail={`${copy(language, '保护状态', 'Protection')} ${protection.protected} / ${protection.total}`} />
    </div>
    <Panel title={copy(language, '自动交易代理托管仓位', 'Agent-managed Positions')} eyebrow={copy(language, '交易代理拥有并负责保护', 'Owned and protected by the trading Agent')}>
      {!managed.length ? <Empty text={copy(language, '当前没有自动交易代理托管仓位。', 'There are no Agent-managed positions.')} /> : <div className="managed-position-grid">{managed.map((position, index) => <article className="managed-position-card" key={String(position.plan_id ?? index)}>
        <header><div><b>{String(position.symbol ?? '—')}</b><StateTag value="AGENT" language={language} /></div><StateTag value={position.protection_state ?? 'UNKNOWN'} language={language} /></header>
        <dl><dt>{copy(language, '数量', 'Quantity')}</dt><dd>{formatQuantity(position.quantity, language)}</dd><dt>{copy(language, '入场价', 'Entry')}</dt><dd>{formatPrice(position.entry_price, language)}</dd><dt>{copy(language, '当前价', 'Current')}</dt><dd>{formatPrice(position.current_price, language)}</dd><dt>{copy(language, '未实现损益', 'Unrealized PnL')}</dt><dd>{formatPnL(position.unrealized_pnl, language)}</dd><dt>{copy(language, '止损价', 'SL')}</dt><dd>{formatPrice(position.stop, language)}</dd><dt>{copy(language, '止盈价', 'TP')}</dt><dd>{formatPrice(position.take_profit, language)}</dd></dl>
        <details className="advanced-details"><summary>{copy(language, '仓位详情', 'Position details')}</summary><ObjectView value={position} language={language} empty="—" /></details>
      </article>)}</div>}
    </Panel>
    <Panel title={copy(language, '外部钱包资产', 'External Wallet Inventory')} eyebrow={copy(language, '计入账户风险，但不会由自动交易代理接管或全部平仓', 'Counts toward account risk, but is not managed or flattened by the Agent')} actions={<button className="button secondary" onClick={() => setHideDust((current) => !current)}>{hideDust ? copy(language, '显示小额资产', 'Show dust') : copy(language, '隐藏小额资产', 'Hide dust')}</button>}>
      <div className="external-safe-note"><b>{copy(language, '来源：外部 · 是否托管：否', 'Origin: External · Managed: No')}</b><span>{copy(language, '外部资产会计入账户风险，但不会被自动交易代理出售、接管或全部平仓。', 'External assets count toward account risk but are never sold, adopted, or flattened by the Agent.')}</span></div>
      {hideDust && hiddenCount > 0 && <p className="dust-summary">{copy(language, `${hiddenCount} 个小额外部资产已隐藏`, `${hiddenCount} small external assets hidden`)}</p>}
      {!visibleExternal.length ? <Empty text={hideDust && hiddenCount ? copy(language, '所有外部资产均为已隐藏的小额资产。', 'All external assets are hidden dust.') : copy(language, '暂无外部钱包资产。', 'No external wallet inventory.')} /> : <div className="inventory-list">{visibleExternal.map((item, index) => <article key={String(item.currency ?? index)}><b>{String(item.currency ?? '—')}</b><span>{formatQuantity(item.quantity, language)}</span><StateTag value={item.origin ?? 'EXTERNAL'} language={language} /><small>{copy(language, '托管：否', 'Managed: No')}</small></article>)}</div>}
    </Panel>
  </>
}

export function TradesView({ rows, language }: { rows: Record<string, unknown>[]; language: Language }) {
  return <Panel title={copy(language, '已关闭交易', 'Closed Trade Summary')} eyebrow={`${rows.length} ${t(language, 'records')}`}>
    {!rows.length ? <Empty text={copy(language, '暂无已关闭交易。', 'No closed trades yet.')} /> : <div className="table-wrap"><table><thead><tr><th>{copy(language, '时间', 'Time')}</th><th>{copy(language, '交易对', 'Symbol')}</th><th>{copy(language, '方向', 'Side')}</th><th>{copy(language, '入场 / 出场', 'Entry / exit')}</th><th>{copy(language, '数量', 'Size')}</th><th>{copy(language, '毛损益', 'Gross PnL')}</th><th>{copy(language, '费用', 'Fees')}</th><th>{copy(language, '净损益', 'Net PnL')}</th><th>{copy(language, '持仓时间', 'Holding time')}</th><th>{copy(language, '退出原因', 'Exit reason')}</th></tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id ?? row.plan_id ?? index)}><td>{formatTimestamp(row.exit_time_ms ?? row.timestamp_ms, language)}</td><td><b>{String(row.symbol ?? '—')}</b></td><td>{translateCode(language, row.side ?? 'UNKNOWN')}</td><td>{formatPrice(row.entry_price ?? row.entry, language)} / {formatPrice(row.exit_price ?? row.exit, language)}</td><td>{formatQuantity(row.quantity ?? row.size, language)}</td><td>{formatPnL(row.gross_pnl, language)}</td><td>{formatUSDT(row.fees, language)}</td><td className={(numeric(row.net_pnl ?? row.pnl) ?? 0) > 0 ? 'positive' : (numeric(row.net_pnl ?? row.pnl) ?? 0) < 0 ? 'negative' : ''}>{formatPnL(row.net_pnl ?? row.pnl, language)}</td><td>{formatDuration(row.holding_time_seconds, language)}</td><td>{translateCode(language, row.exit_reason ?? 'UNKNOWN')}</td></tr>)}</tbody></table></div>}
    {rows.length > 0 && <details className="advanced-details"><summary>{copy(language, '交易编号与完整字段', 'Trade IDs and full fields')}</summary><ObjectView value={rows} language={language} empty="—" /></details>}
  </Panel>
}

const percentageRiskKeys = new Set(['risk_per_trade', 'max_position_pct', 'max_total_exposure_pct', 'max_daily_loss_pct'])
const percentagePointKeys = new Set(['max_spread_pct', 'max_slippage_pct', 'max_entry_deviation_pct'])

function ConfigGrid({ value, language }: { value: Record<string, unknown>; language: Language }) {
  return <dl className="config-grid">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{fieldLabel(language, key)}</dt><dd>{percentageRiskKeys.has(key) ? formatPercent(item, language) : percentagePointKeys.has(key) ? formatPercent(item, language, false) : displayPrimitive(item, language)}</dd></div>)}</dl>
}

export function SettingsView({ data, control, language }: { data: Record<string, unknown> | undefined; control?: ControlState; language: Language }) {
  const settings = asRecord(data)
  const runtime = asRecord(settings.runtime_display)
  return <>
    <Panel title={copy(language, '实时运行架构', 'Realtime Runtime')} eyebrow={copy(language, '只读状态 · 非 15 秒行情轮询', 'Read-only · not a 15-second market poll')}>
      <dl className="runtime-grid">
        <div><dt>{copy(language, '实时行情', 'Realtime market')}</dt><dd>{translateCode(language, runtime.realtime_market_enabled ?? 'ENABLED')}</dd></div>
        <div><dt>{copy(language, '市场来源', 'Market source')}</dt><dd>{translateCode(language, runtime.market_source ?? 'OKX Public WebSocket')}</dd></div>
        <div><dt>{copy(language, '策略触发', 'Strategy trigger')}</dt><dd>{translateCode(language, runtime.strategy_trigger ?? 'Confirmed 1m Candle')}</dd></div>
        <div><dt>{copy(language, '确认周期', 'Confirmations')}</dt><dd>{(Array.isArray(runtime.confirmations) ? runtime.confirmations : ['3m', '5m']).map((item) => translateCode(language, item)).join(' / ')}</dd></div>
        <div><dt>{copy(language, '账户同步', 'Account sync')}</dt><dd>{formatDuration(runtime.account_sync_interval_seconds ?? 3, language)}</dd></div>
        <div><dt>{copy(language, '订单快速核对', 'Order fast lane')}</dt><dd>{translateCode(language, runtime.targeted_reconciliation ?? 'ENABLED')}</dd></div>
        <div><dt>{copy(language, '后台健康检查间隔', 'Runtime watchdog interval')}</dt><dd>{formatDuration(asRecord(settings.runtime).scan_interval_seconds ?? control?.scan_interval_seconds, language)}</dd></div>
      </dl>
      <p className="semantic-note">{copy(language, '“后台健康检查间隔”是兼容性监控设置，不代表行情每 15 秒才刷新。交易策略由实时 WebSocket 的已确认 1 分钟 K 线触发。', 'The runtime watchdog interval is a compatibility/health setting; it does not mean market data refreshes every 15 seconds. Strategy evaluation is triggered by confirmed 1m candles from the realtime WebSocket.')}</p>
    </Panel>
    <div className="two-column"><Panel title={copy(language, '风险参数', 'Risk Parameters')} eyebrow={control?.session_state === 'STOPPED' ? copy(language, '只读配置', 'Read-only configuration') : copy(language, '自动交易会话运行中 · 已锁定', 'Session active · locked')}><ConfigGrid value={asRecord(settings.risk)} language={language} /></Panel><Panel title={copy(language, '策略与执行参数', 'Strategy & Execution Parameters')} eyebrow={copy(language, '生产策略 scalping_v1_baseline · 本轮未调参', 'Production strategy scalping_v1_baseline · unchanged')}><ConfigGrid value={{ ...asRecord(settings.scalping), ...asRecord(settings.execution) }} language={language} /></Panel></div>
    <details className="advanced-details panel"><summary>{copy(language, '高级运行配置', 'Advanced Runtime Configuration')}</summary><ObjectView value={settings} language={language} empty={t(language, 'dataUnavailable')} /></details>
  </>
}

export function LogsAuditView({ logs, audit, language }: { logs: string[]; audit: Record<string, unknown>[]; language: Language }) {
  const [filter, setFilter] = useState('ALL')
  const filters = ['ALL', 'TRADING', 'RISK', 'ORDER', 'RECONCILIATION', 'MARKET', 'CONTROL', 'ERROR']
  const filteredAudit = useMemo(() => audit.filter((item) => {
    if (filter === 'ALL') return true
    const text = `${item.requested_action ?? ''} ${item.reason ?? ''}`.toUpperCase()
    const mappings: Record<string, string[]> = {
      TRADING: ['SESSION', 'AUTO', 'TRADE'], RISK: ['RISK', 'KILL', 'EXPOSURE', 'PROTECTION'], ORDER: ['ORDER', 'SUBMIT', 'CANCEL'],
      RECONCILIATION: ['RECONCILIATION', 'RECONCILE'], MARKET: ['MARKET', 'SCAN'], CONTROL: ['MODE', 'AGENT', 'EXECUTION', 'CONTROL'], ERROR: ['FAIL', 'ERROR', 'UNKNOWN', 'UNAVAILABLE'],
    }
    return mappings[filter]?.some((word) => text.includes(word)) ?? false
  }), [audit, filter])
  return <><div className="filter-bar" role="group" aria-label={copy(language, '日志筛选', 'Log filters')}>{filters.map((item) => <button key={item} className={filter === item ? 'active' : ''} onClick={() => setFilter(item)}>{translateCode(language, item)}</button>)}</div><div className="two-column"><Panel title={copy(language, '原始运行日志', 'Raw Runtime Log')} eyebrow={`${logs.length} ${t(language, 'lines')}`}><pre className="log-view">{logs.join('\n') || '—'}</pre></Panel><Panel title={copy(language, '控制与审计事件', 'Control & Audit Events')} eyebrow={`${filteredAudit.length} ${t(language, 'events')}`}><div className="audit-list">{filteredAudit.length ? filteredAudit.map((item, index) => <article key={String(item.id ?? index)}><small>{formatTimestamp(item.timestamp_ms, language)}</small><b>{translateCode(language, item.requested_action)}</b><span>{translateCode(language, item.reason)}</span><details><summary>{copy(language, '状态详情', 'State details')}</summary><ObjectView value={{ previous_state: item.previous_state_json, result_state: item.result_state_json }} language={language} empty="—" /></details></article>) : <Empty text={t(language, 'noAudit')} />}</div></Panel></div></>
}

export function GenericTable({ title, rows, language }: { title: string; rows: Record<string, unknown>[]; language: Language }) {
  const keys = useMemo(() => Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).filter((key) => !key.endsWith('_json')).slice(0, 8), [rows])
  return <Panel title={title} eyebrow={`${rows.length} ${t(language, 'records')}`}>{rows.length ? <div className="table-wrap"><table><thead><tr>{keys.map((key) => <th key={key}>{fieldLabel(language, key)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id ?? row.plan_id ?? index)}>{keys.map((key) => <td key={key}>{key.endsWith('_ms') ? formatTimestamp(row[key], language) : row[key] && typeof row[key] === 'object' ? <details className="row-details"><summary>{copy(language, '查看', 'View')}</summary><ObjectView value={row[key]} language={language} empty="—" /></details> : displayPrimitive(row[key], language)}</td>)}</tr>)}</tbody></table></div> : <Empty text={t(language, 'noRecords')} />}</Panel>
}
