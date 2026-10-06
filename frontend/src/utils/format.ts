const pad = (value: number) => String(value).padStart(2, '0')

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** YYYY-MM-DD HH:mm */
export function formatDateTime(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return '—'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** YYYY-MM-DD HH:mm:ss */
export function formatFullDateTime(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return '—'
  return `${formatDateTime(value)}:${pad(date.getSeconds())}`
}

/** MM-DD HH:mm, with the year only when it differs from the current one. */
export function formatShortDateTime(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return '—'
  const prefix = date.getFullYear() === new Date().getFullYear() ? '' : `${date.getFullYear()}-`
  return `${prefix}${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

const relativeFormatter = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' })

export function formatRelative(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return '—'
  const seconds = Math.round((date.getTime() - Date.now()) / 1000)
  if (Math.abs(seconds) < 60) return relativeFormatter.format(seconds, 'second')
  const minutes = Math.round(seconds / 60)
  if (Math.abs(minutes) < 60) return relativeFormatter.format(minutes, 'minute')
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 24) return relativeFormatter.format(hours, 'hour')
  const days = Math.round(hours / 24)
  if (Math.abs(days) < 7) return relativeFormatter.format(days, 'day')
  return formatDateTime(value).slice(0, 10)
}

/** Human readable span, e.g. "3 天 4 小时" or "12 分钟". */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds))
  const days = Math.floor(seconds / 86_400)
  const hours = Math.floor((seconds % 86_400) / 3_600)
  const minutes = Math.floor((seconds % 3_600) / 60)
  if (days > 0) return hours > 0 ? `${days} 天 ${hours} 小时` : `${days} 天`
  if (hours > 0) return minutes > 0 ? `${hours} 小时 ${minutes} 分钟` : `${hours} 小时`
  if (minutes > 0) return `${minutes} 分钟`
  return `${seconds} 秒`
}

export function isToday(value: string | null | undefined): boolean {
  const date = parseDate(value)
  if (!date) return false
  const today = new Date()
  return (
    date.getFullYear() === today.getFullYear() &&
    date.getMonth() === today.getMonth() &&
    date.getDate() === today.getDate()
  )
}

export function shortIdentifier(value: string): string {
  if (value.length <= 20) return value
  return `${value.slice(0, 8)}…${value.slice(-6)}`
}
