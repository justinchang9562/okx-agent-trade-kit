import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { highRiskWritesAllowed, resolveTheme, SafetyBar } from './App'
import { fieldLabel, t, translateCode } from './locales'
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

describe('localized appearance preferences', () => {
  it('resolves system, light, and dark themes deterministically', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })

  it('provides Chinese and English navigation language', () => {
    expect(t('zh', 'overview')).toBe('总览')
    expect(t('en', 'overview')).toBe('Overview')
    expect(t('zh', 'theme')).toBe('颜色主题')
    expect(t('en', 'theme')).toBe('Theme')
  })

  it('localizes user-facing runtime values and risk reasons in Chinese', () => {
    expect(translateCode('zh', 'DEMO')).toBe('模拟盘')
    expect(translateCode('zh', 'STOPPED')).toBe('已停止')
    expect(translateCode('zh', 'DISARMED')).toBe('未授权')
    expect(translateCode('zh', 'CONNECTED')).toBe('已连接')
    expect(translateCode('zh', 'MAX_TOTAL_EXPOSURE_REACHED')).toBe('已达到最大总风险敞口')
    expect(translateCode('en', 'MAX_TOTAL_EXPOSURE_REACHED')).toBe('MAX_TOTAL_EXPOSURE_REACHED')
  })

  it('localizes dynamic data field labels', () => {
    expect(fieldLabel('zh', 'managed_positions')).toBe('代理托管仓位')
    expect(fieldLabel('zh', 'average_fill_price')).toBe('平均成交价')
    expect(fieldLabel('en', 'average_fill_price')).toBe('Average fill price')
  })

  it('renders the full OKX mark and localized safety states', () => {
    render(<SafetyBar control={{ ...control, connection_state: 'CONNECTED' }} stream="CONNECTED" language="zh" />)
    const safety = within(screen.getByLabelText('交易安全状态'))
    expect(safety.getByText('OKX')).toBeInTheDocument()
    expect(safety.getByText('模拟盘')).toBeInTheDocument()
    expect(safety.getByText('已停止')).toBeInTheDocument()
    expect(safety.getByText('未授权')).toBeInTheDocument()
    expect(safety.getByText('已连接')).toBeInTheDocument()
  })
})
