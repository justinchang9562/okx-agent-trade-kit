import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { flattenControlVisible, highRiskWritesAllowed, resolveTheme, SafetyBar } from './App'
import { fieldLabel, t, translateCode } from './locales'
import type { ControlState } from './types'

const control: ControlState = {
  environment: 'DEMO',
  live_setup_state: 'NOT_CONFIGURED',
  execution_state: 'DISARMED',
  session_state: 'STOPPED',
  agent_runtime_state: 'STOPPED',
  trading_mode: 'STOPPED',
  connection_state: 'DISCONNECTED',
  kill_switch_active: false,
  auto_demo_enabled: false,
  scan_interval_seconds: 15,
  updated_at_ms: 0,
}

describe('SafetyBar', () => {
  it('shows the simplified session and stream safety state', () => {
    const html = renderToStaticMarkup(<SafetyBar control={control} stream="STALE" />)
    expect(html).toContain('DEMO')
    expect(html).toContain('STOPPED')
    expect(html).toContain('STALE')
  })

  it('fails closed when the WebSocket is stale', () => {
    expect(highRiskWritesAllowed({ ...control, connection_state: 'CONNECTED' }, 'CONNECTED')).toBe(true)
    expect(highRiskWritesAllowed({ ...control, connection_state: 'CONNECTED' }, 'STALE')).toBe(false)
    expect(highRiskWritesAllowed({ ...control, connection_state: 'STALE' }, 'CONNECTED')).toBe(false)
  })

  it('keeps flatten available for an active session even before a position appears', () => {
    expect(flattenControlVisible('RUNNING', 0)).toBe(true)
    expect(flattenControlVisible('STOPPED', 1)).toBe(true)
    expect(flattenControlVisible('STOPPED', 0)).toBe(false)
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
    const html = renderToStaticMarkup(
      <SafetyBar control={{ ...control, connection_state: 'CONNECTED' }} stream="CONNECTED" language="zh" />,
    )
    expect(html).toContain('交易安全状态')
    expect(html).toContain('OKX')
    expect(html).toContain('模拟盘')
    expect(html).toContain('已停止')
    expect(html).not.toContain('>实时<')
    expect(html.match(/已连接/g)).toHaveLength(2)
  })
})
