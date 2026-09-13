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

export function isoDateInDays(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}
