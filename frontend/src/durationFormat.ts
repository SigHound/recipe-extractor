/**
 * Turn schema.org / ISO-8601 durations (e.g. PT25M, PT275M) into readable text.
 * Non-ISO strings are returned trimmed as-is.
 */

function formatMinutesHuman(totalMinutes: number): string {
  const rounded = Math.round(totalMinutes)
  if (rounded <= 0) return '—'
  if (rounded < 60) return `${rounded} min`
  const h = Math.floor(rounded / 60)
  const m = rounded % 60
  if (m === 0) return h === 1 ? '1 hr' : `${h} hr`
  return h === 1 ? `1 hr ${m} min` : `${h} hr ${m} min`
}

/** Parse PnDTnHnMnS (subset) to total minutes. Returns null if not a recognizable ISO duration. */
function parseIso8601DurationToMinutes(iso: string): number | null {
  const u = iso.trim().toUpperCase()
  if (!u.startsWith('P')) return null

  const m = u.match(
    /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$/i,
  )
  if (!m) return null

  const days = m[1] ? parseInt(m[1], 10) : 0
  const hours = m[2] ? parseInt(m[2], 10) : 0
  const minutes = m[3] ? parseInt(m[3], 10) : 0
  const seconds = m[4] ? parseFloat(m[4]) : 0

  if (!m[1] && !m[2] && !m[3] && !m[4] && u !== 'P0D' && u !== 'PT0S') {
    if (u === 'P' || u === 'PT') return null
  }

  let total = days * 24 * 60 + hours * 60 + minutes + seconds / 60
  if (!Number.isFinite(total) || total < 0) return null
  return total
}

export function formatRecipeDuration(raw: string | null | undefined): string {
  if (raw == null) return '—'
  const s = raw.trim()
  if (s === '') return '—'

  const parsed = parseIso8601DurationToMinutes(s)
  if (parsed != null) return formatMinutesHuman(parsed)

  return s
}
