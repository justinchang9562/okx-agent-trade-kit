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
  it('shows the independent mode, execution, emergency stop, and stream states', () => {
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
    expect(fieldLabel('zh', 'connection_freshness_age_seconds')).toBe('连接数据延迟（秒）')
    expect(fieldLabel('zh', 'last_scan_at_ms')).toBe('上次市场扫描时间')
    expect(fieldLabel('zh', 'last_health_at_ms')).toBe('上次系统检查时间')
    expect(fieldLabel('zh', 'consecutive_losses')).toBe('连续亏损次数')
    expect(fieldLabel('zh', 'external_wallet_inventory')).toBe('外部钱包资产')
    expect(fieldLabel('zh', 'origin')).toBe('资产来源')
    expect(fieldLabel('zh', 'managed')).toBe('是否由代理管理')
    expect(fieldLabel('en', 'average_fill_price')).toBe('Average fill price')
  })

  it('renders the full OKX mark and unambiguous localized safety labels', () => {
    const html = renderToStaticMarkup(
      <SafetyBar control={{ ...control, connection_state: 'CONNECTED' }} stream="CONNECTED" language="zh" />,
    )
    expect(html).toContain('交易安全状态')
    expect(html).toContain('OKX')
    expect(html).toContain('环境')
    expect(html).toContain('模式')
    expect(html).toContain('执行')
    expect(html).toContain('紧急停止')
    expect(html).toContain('数据流')
    expect(html).toContain('模拟盘')
    expect(html).toContain('已停止')
    expect(html).not.toContain('>实时<')
    expect(html.match(/已连接/g)).toHaveLength(1)
  })
})
