import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { highRiskWritesAllowed, SafetyBar } from './App'
import type { ControlState } from './types'

const control: ControlState = {
  environment: 'DEMO',
  live_setup_state: 'NOT_CONFIGURED',
  execution_state: 'DISARMED',
  agent_runtime_state: 'STOPPED',
  trading_mode: 'STOPPED',
  connection_state: 'DISCONNECTED',
  kill_switch_active: false,
  auto_demo_enabled: false,
  scan_interval_seconds: 15,
  updated_at_ms: 0,
}

describe('SafetyBar', () => {
  it('shows independent execution and stream safety state', () => {
    render(<SafetyBar control={control} stream="STALE" />)
    expect(screen.getByText('DEMO')).toBeInTheDocument()
    expect(screen.getByText('DISARMED')).toBeInTheDocument()
    expect(screen.getByText('STALE')).toBeInTheDocument()
  })

  it('fails closed when the WebSocket is stale', () => {
    expect(highRiskWritesAllowed({ ...control, connection_state: 'CONNECTED' }, 'CONNECTED')).toBe(true)
    expect(highRiskWritesAllowed({ ...control, connection_state: 'CONNECTED' }, 'STALE')).toBe(false)
    expect(highRiskWritesAllowed({ ...control, connection_state: 'STALE' }, 'CONNECTED')).toBe(false)
  })
})
