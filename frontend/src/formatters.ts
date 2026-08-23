import type { Language } from './locales'

const locale = (language: Language) => language === 'zh' ? 'zh-CN' : 'en-US'

export function numeric(value: unknown): number | undefined {
  const parsed = typeof value === 'number'
    ? value
    : typeof value === 'string' && value.trim() !== ''
      ? Number(value)
      : Number.NaN
  return Number.isFinite(parsed) ? parsed : undefined
}

export function formatOptional(
  value: unknown,
  formatter: (numberValue: number) => string,
  unavailable = '—',
): string {
  const parsed = numeric(value)
  return parsed === undefined ? unavailable : formatter(parsed)
}

export function formatNumber(
  value: unknown,
  language: Language,
  digits = 2,
  minimumDigits = digits,
): string {
  return formatOptional(value, (parsed) => parsed.toLocaleString(locale(language), {
    minimumFractionDigits: minimumDigits,
    maximumFractionDigits: digits,
  }))
}

export function formatPrice(value: unknown, language: Language): string {
  const parsed = numeric(value)
  if (parsed === undefined) return '—'
  const digits = parsed >= 1_000 ? 2 : parsed >= 1 ? 4 : 8
  return parsed.toLocaleString(locale(language), {
    minimumFractionDigits: 2,
    maximumFractionDigits: digits,
  })
}

export function formatQuantity(value: unknown, language: Language): string {
  return formatOptional(value, (parsed) => parsed.toLocaleString(locale(language), {
    minimumFractionDigits: parsed === 0 ? 2 : 0,
    maximumFractionDigits: 8,
  }))
}

export function formatUSDT(value: unknown, language: Language): string {
  return formatOptional(value, (parsed) => `${parsed.toLocaleString(locale(language), {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} USDT`)
}

export function formatPercent(
  value: unknown,
  language: Language,
  fraction = true,
  digits = 2,
): string {
  return formatOptional(value, (parsed) => `${(fraction ? parsed * 100 : parsed).toLocaleString(locale(language), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}%`)
}

export function formatPnL(value: unknown, language: Language): string {
  return formatOptional(value, (parsed) => {
    const amount = Math.abs(parsed).toLocaleString(locale(language), {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
    return `${parsed > 0 ? '+' : parsed < 0 ? '-' : ''}${amount} USDT`
  })
}

export function formatTimestamp(value: unknown, language: Language): string {
  const parsed = numeric(value)
  if (parsed === undefined) return '—'
  const date = new Date(parsed)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(locale(language), {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date)
}

export function formatTime(value: unknown, language: Language): string {
  const parsed = numeric(value)
  if (parsed === undefined) return '—'
  const date = new Date(parsed)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(locale(language), {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date)
}

export function formatDuration(value: unknown, language: Language): string {
  const seconds = numeric(value)
  if (seconds === undefined) return '—'
  if (seconds < 60) return `${formatNumber(seconds, language, 1, 1)}s`
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.floor(seconds % 60)
  return language === 'zh' ? `${minutes}分 ${remainder}秒` : `${minutes}m ${remainder}s`
}

export function formatAge(value: unknown, language: Language): string {
  return formatOptional(value, (seconds) => `${seconds.toLocaleString(locale(language), {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}s`)
}

export function formatIdentifier(value: unknown): string {
  const raw = value === undefined || value === null ? '' : String(value)
  if (!raw) return '—'
  return raw.length <= 12 ? raw : `${raw.slice(0, 7)}…${raw.slice(-4)}`
}
