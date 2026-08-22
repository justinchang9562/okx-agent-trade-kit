export type Connection = 'CONNECTED' | 'STALE' | 'DISCONNECTED'
export type TradingMode = 'STOPPED' | 'DRY_RUN' | 'MANUAL_APPROVAL' | 'AUTO'

export interface ControlState {
  environment: 'DEMO' | 'LIVE'
  live_setup_state: string
  execution_state: 'DISARMED' | 'ARMED'
  agent_runtime_state: 'STOPPED' | 'RUNNING' | 'DEGRADED' | 'STALE'
  trading_mode: TradingMode
  connection_state: Connection
  kill_switch_active: boolean
  auto_demo_enabled: boolean
  scan_interval_seconds: number
  updated_at_ms: number
}

export interface StatusPayload {
  schema_version: string
  control: ControlState
  core: Record<string, unknown>
  live: { setup_state: string; execution: string }
}

export interface Plan {
  plan_id: string
  symbol: string
  decision?: string
  ui_status?: string
  status: string
  signal_score?: number
  signal_strength?: number
  entry?: number
  stop?: number
  take_profit?: number
  position_size?: number
  estimated_usdt?: number
  expires_at_ms: number
  rejection_reason?: string | null
}

export interface DashboardData {
  status?: StatusPayload
  health?: Record<string, unknown>
  account?: Record<string, unknown>
  scanner?: Record<string, unknown>
  signals: Record<string, unknown>[]
  plans: Plan[]
  orders?: Record<string, unknown>
  positions?: Record<string, unknown>
  trades: Record<string, unknown>[]
  logs: string[]
  audit: Record<string, unknown>[]
  settings?: Record<string, unknown>
}

export interface StreamEvent {
  schema_version: string
  sequence: number
  timestamp_ms: number
  type: string
  reason: string
  data: Record<string, unknown>
}
