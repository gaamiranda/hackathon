/** Format a money string ("27618.42") with thousands separators without float parsing. */
export function money(value: string | null | undefined, currency?: string): string {
  if (value == null) return '—'
  const [whole, frac] = value.split('.')
  const sign = whole.startsWith('-') ? '-' : ''
  const digits = whole.replace('-', '')
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const out = `${sign}${grouped}${frac !== undefined ? '.' + frac.padEnd(2, '0').slice(0, 2) : '.00'}`
  return currency ? `${currency} ${out}` : out
}

export function time(ts: string): string {
  return new Date(ts).toLocaleTimeString(undefined, { hour12: false })
}

/**
 * Today (UTC) + `days`, as YYYY-MM-DD. UTC on purpose: the backend stamps `created_at` in UTC and the delivery
 * window is `required_by − created_at`, so the demo preset must give the same window (14 d) from any timezone at
 * any hour — otherwise the Decision Agent's prompt, and its cache key, would differ from the recorded demo (T18).
 */
export function isoDateInDays(days: number): string {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}
