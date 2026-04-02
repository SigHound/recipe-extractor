/**
 * Convert cooking quantities between metric and imperial (mass, volume) and °C ↔ °F.
 * Skips lines that look like pan/tool dimensions (inches, cm, 9x13, etc.).
 */

export type UnitMode = 'metric' | 'imperial'

const ML_PER_CUP = 236.5882365
const ML_PER_TBSP = 14.78676478125
const ML_PER_TSP = 4.92892159375
const ML_PER_FLOZ = 29.5735295625
const G_PER_LB = 453.59237
const G_PER_OZ = 28.349523125

/** Pan, dish, or tool sizes — do not convert these lines. */
export function shouldSkipUnitConversion(line: string): boolean {
  const s = line.trim()
  if (!s) return true
  if (/\d\s*[-x×]\s*\d/.test(s)) return true
  if (/\b\d+(?:\.\d+)?\s*[-]?\s*(?:inch|inches|in\.|")\b/i.test(s)) return true
  if (/\b\d+(?:\.\d+)?\s*[-]?\s*cm\b/i.test(s)) return true
  return false
}

function roundSmart(n: number, decimals: number): string {
  const f = 10 ** decimals
  const v = Math.round(n * f) / f
  return Number.isInteger(v) ? String(v) : String(v.toFixed(decimals)).replace(/\.?0+$/, '')
}

function formatMetricVolume(ml: number): string {
  if (ml >= 1000) return `${roundSmart(ml / 1000, 2)} L`
  if (ml >= 100) return `${Math.round(ml / 5) * 5} ml`
  return `${Math.round(ml)} ml`
}

function formatImperialVolume(ml: number): string {
  if (ml < 0.5) return `${Math.round(ml)} ml`
  let x = ml
  const cups = Math.floor(x / ML_PER_CUP)
  x -= cups * ML_PER_CUP
  const tbsp = Math.floor(x / ML_PER_TBSP)
  x -= tbsp * ML_PER_TBSP
  let tsp = Math.round(x / ML_PER_TSP)
  if (tsp >= 3) {
    const addTbsp = Math.floor(tsp / 3)
    tsp -= addTbsp * 3
    const newTbsp = tbsp + addTbsp
    const parts: string[] = []
    if (cups > 0) parts.push(`${cups} cup${cups === 1 ? '' : 's'}`)
    if (newTbsp > 0) parts.push(`${newTbsp} tbsp`)
    if (tsp > 0) parts.push(`${tsp} tsp`)
    return parts.join(' ') || `${roundSmart(ml / ML_PER_FLOZ, 1)} fl oz`
  }
  const parts: string[] = []
  if (cups > 0) parts.push(`${cups} cup${cups === 1 ? '' : 's'}`)
  if (tbsp > 0) parts.push(`${tbsp} tbsp`)
  if (tsp > 0) parts.push(`${tsp} tsp`)
  if (parts.length > 0) return parts.join(' ')
  if (ml >= ML_PER_FLOZ * 0.25) return `${roundSmart(ml / ML_PER_FLOZ, 1)} fl oz`
  return `${Math.round(ml)} ml`
}

function formatMetricMass(g: number): string {
  if (g >= 1000) return `${roundSmart(g / 1000, 2)} kg`
  if (g >= 100) return `${Math.round(g)} g`
  if (g >= 10) return `${Math.round(g)} g`
  return `${roundSmart(g, 1)} g`
}

function formatImperialMass(g: number): string {
  if (g >= G_PER_LB - 0.01) {
    const lb = Math.floor(g / G_PER_LB)
    const remOz = (g - lb * G_PER_LB) / G_PER_OZ
    if (remOz < 0.05) return `${lb} lb`
    return `${lb} lb ${roundSmart(remOz, 1)} oz`
  }
  return `${roundSmart(g / G_PER_OZ, 1)} oz`
}

function formatMetricVolumeRange(ml1: number, ml2: number): string {
  const hi = Math.max(ml1, ml2)
  if (hi >= 1000) {
    return `${roundSmart(ml1 / 1000, 2)} to ${roundSmart(ml2 / 1000, 2)} L`
  }
  return `${Math.round(ml1)} to ${Math.round(ml2)} ml`
}

function formatMetricMassRange(g1: number, g2: number): string {
  const hi = Math.max(g1, g2)
  if (hi >= 1000) {
    return `${roundSmart(g1 / 1000, 2)} to ${roundSmart(g2 / 1000, 2)} kg`
  }
  return `${Math.round(g1)} to ${Math.round(g2)} g`
}

function fahrenheitToCelsius(f: number): number {
  return ((f - 32) * 5) / 9
}

function celsiusToFahrenheit(c: number): number {
  return (c * 9) / 5 + 32
}

function formatMetricTemp(baseC: number): string {
  return `${Math.round(baseC)}°C`
}

function formatImperialTemp(baseC: number): string {
  return `${Math.round(celsiusToFahrenheit(baseC))}°F`
}

function parseLeadingNumber(s: string, i: number): { n: number; end: number } | null {
  const sub = s.slice(i)
  const m = sub.match(
    /^(\d+)\s+(\d+)\/(\d+)|^(\d+)\/(\d+)|^(\d+(?:\.\d+)?)|^([½⅓⅔¼¾⅛])/
  )
  if (!m) return null
  if (m[1] != null) {
    const den = parseInt(m[3], 10)
    if (den === 0) return null
    return { n: parseInt(m[1], 10) + parseInt(m[2], 10) / den, end: i + m[0].length }
  }
  if (m[4] != null) {
    const den = parseInt(m[5], 10)
    if (den === 0) return null
    return { n: parseInt(m[4], 10) / den, end: i + m[0].length }
  }
  if (m[6] != null) return { n: parseFloat(m[6]), end: i + m[0].length }
  const u: Record<string, number> = { '½': 0.5, '⅓': 1 / 3, '⅔': 2 / 3, '¼': 0.25, '¾': 0.75, '⅛': 0.125 }
  if (m[7] != null) return { n: u[m[7]] ?? 0, end: i + m[0].length }
  return null
}

type FoundUnit =
  | { kind: 'vol'; baseMl: number; start: number; end: number }
  | { kind: 'volRange'; baseMl1: number; baseMl2: number; start: number; end: number }
  | { kind: 'mass'; baseG: number; start: number; end: number }
  | { kind: 'massRange'; baseG1: number; baseG2: number; start: number; end: number }
  | { kind: 'temp'; baseC: number; start: number; end: number }

/** Match °F / °C after a number. `fl oz` is excluded (handled by volume patterns first). */
function tryMatchTemperature(rest: string, num: { n: number; end: number }, startIdx: number): FoundUnit | null {
  const degF = rest.match(/^\s*degrees?\s+Fahrenheit\b/i)
  if (degF) {
    return {
      kind: 'temp',
      baseC: fahrenheitToCelsius(num.n),
      start: startIdx,
      end: num.end + degF[0].length,
    }
  }
  const degC = rest.match(/^\s*degrees?\s+Celsius\b/i)
  if (degC) {
    return { kind: 'temp', baseC: num.n, start: startIdx, end: num.end + degC[0].length }
  }
  const degFshort = rest.match(/^\s*degrees?\s+F\b/i)
  if (degFshort) {
    return {
      kind: 'temp',
      baseC: fahrenheitToCelsius(num.n),
      start: startIdx,
      end: num.end + degFshort[0].length,
    }
  }
  const degCshort = rest.match(/^\s*degrees?\s+C\b/i)
  if (degCshort) {
    return { kind: 'temp', baseC: num.n, start: startIdx, end: num.end + degCshort[0].length }
  }

  const fPatterns = [
    /^\s*°\s*F(?:ahrenheit)?\b/i,
    /^\s*°F\b/i,
    /^\s+F(?:ahrenheit)?\b/i,
    /^\s+F\b(?![a-z])/i,
  ]
  for (const re of fPatterns) {
    const m = rest.match(re)
    if (!m) continue
    const baseC = fahrenheitToCelsius(num.n)
    return { kind: 'temp', baseC, start: startIdx, end: num.end + m[0].length }
  }
  const cPatterns = [
    /^\s*°\s*C(?:elsius)?\b/i,
    /^\s*°C\b/i,
    /^\s+C(?:elsius)?\b/i,
    /^\s+C\b(?![a-z])/i,
  ]
  for (const re of cPatterns) {
    const m = rest.match(re)
    if (!m) continue
    return { kind: 'temp', baseC: num.n, start: startIdx, end: num.end + m[0].length }
  }
  return null
}

const UNIT_SPECS: {
  re: RegExp
  kind: 'vol' | 'mass'
  toBase: (n: number) => number
}[] = [
  { re: /^\s*(fl\.?\s*oz|fluid\s*ounces?)\b/i, kind: 'vol', toBase: (n) => n * ML_PER_FLOZ },
  { re: /^\s*(cups?)\b/i, kind: 'vol', toBase: (n) => n * ML_PER_CUP },
  { re: /^\s*(tbsp|tablespoons?)\b/i, kind: 'vol', toBase: (n) => n * ML_PER_TBSP },
  { re: /^\s*(tsp|teaspoons?)\b/i, kind: 'vol', toBase: (n) => n * ML_PER_TSP },
  { re: /^\s*(ml|mL|milliliters?|millilitres?)\b/i, kind: 'vol', toBase: (n) => n },
  { re: /^\s*(l|L|liters?|litres?)\b/i, kind: 'vol', toBase: (n) => n * 1000 },
  { re: /^\s*(kg|kilograms?|kilos?)\b/i, kind: 'mass', toBase: (n) => n * 1000 },
  { re: /^\s*(g|grams?)\b/i, kind: 'mass', toBase: (n) => n },
  { re: /^\s*(lb|lbs|pounds?)\b/i, kind: 'mass', toBase: (n) => n * G_PER_LB },
  { re: /^\s*(oz|ounces?)\b/i, kind: 'mass', toBase: (n) => n * G_PER_OZ },
]

/**
 * "3 to 4 lb …" / "2 - 3 cups …" — same unit applies to both ends; convert both.
 */
function tryParseQuantityRangeSameUnit(
  line: string,
  startIdx: number,
  num: { n: number; end: number },
): FoundUnit | null {
  const rest = line.slice(num.end)
  const sep = rest.match(/^\s+(?:to|-)\s+/i)
  if (!sep) return null
  const secondStart = num.end + sep[0].length
  const num2 = parseLeadingNumber(line, secondStart)
  if (!num2) return null
  const rest2 = line.slice(num2.end)

  if (tryMatchTemperature(rest2, num2, secondStart)) return null

  for (const spec of UNIT_SPECS) {
    const um = rest2.match(spec.re)
    if (!um) continue
    const unitLen = um[0].length
    const end = num2.end + unitLen
    if (spec.kind === 'vol') {
      return {
        kind: 'volRange',
        baseMl1: spec.toBase(num.n),
        baseMl2: spec.toBase(num2.n),
        start: startIdx,
        end,
      }
    }
    return {
      kind: 'massRange',
      baseG1: spec.toBase(num.n),
      baseG2: spec.toBase(num2.n),
      start: startIdx,
      end,
    }
  }
  return null
}

function findAllQuantityUnits(line: string): FoundUnit[] {
  const out: FoundUnit[] = []
  let i = 0
  while (i < line.length) {
    const num = parseLeadingNumber(line, i)
    if (!num) {
      i += 1
      continue
    }

    const rangeMatch = tryParseQuantityRangeSameUnit(line, i, num)
    if (rangeMatch) {
      out.push(rangeMatch)
      i = rangeMatch.end
      continue
    }

    const rest = line.slice(num.end)
    let matched: FoundUnit | null = null

    const temp = tryMatchTemperature(rest, num, i)
    if (temp) {
      matched = temp
    } else {
      let unitLen = 0
      for (const spec of UNIT_SPECS) {
        const um = rest.match(spec.re)
        if (!um) continue
        unitLen = um[0].length
        const baseVal = spec.toBase(num.n)
        if (spec.kind === 'vol') {
          matched = { kind: 'vol', baseMl: baseVal, start: i, end: num.end + unitLen }
        } else {
          matched = { kind: 'mass', baseG: baseVal, start: i, end: num.end + unitLen }
        }
        break
      }
    }

    if (matched) {
      out.push(matched)
      i = matched.end
    } else {
      i = num.end
    }
  }
  return out
}

function scaleSegments(segs: FoundUnit[], factor: number): FoundUnit[] {
  if (factor === 1 || !Number.isFinite(factor) || factor <= 0) return segs
  return segs.map((seg) => {
    if (seg.kind === 'temp') return seg
    if (seg.kind === 'vol') return { ...seg, baseMl: seg.baseMl * factor }
    if (seg.kind === 'volRange') {
      return {
        ...seg,
        baseMl1: seg.baseMl1 * factor,
        baseMl2: seg.baseMl2 * factor,
      }
    }
    if (seg.kind === 'massRange') {
      return {
        ...seg,
        baseG1: seg.baseG1 * factor,
        baseG2: seg.baseG2 * factor,
      }
    }
    return { ...seg, baseG: seg.baseG * factor }
  })
}

function formatSegment(mode: UnitMode, seg: FoundUnit): string {
  if (seg.kind === 'temp') {
    return mode === 'metric' ? formatMetricTemp(seg.baseC) : formatImperialTemp(seg.baseC)
  }
  if (seg.kind === 'vol') {
    return mode === 'metric' ? formatMetricVolume(seg.baseMl) : formatImperialVolume(seg.baseMl)
  }
  if (seg.kind === 'volRange') {
    return mode === 'metric'
      ? formatMetricVolumeRange(seg.baseMl1, seg.baseMl2)
      : `${formatImperialVolume(seg.baseMl1)} to ${formatImperialVolume(seg.baseMl2)}`
  }
  if (seg.kind === 'massRange') {
    return mode === 'metric'
      ? formatMetricMassRange(seg.baseG1, seg.baseG2)
      : `${formatImperialMass(seg.baseG1)} to ${formatImperialMass(seg.baseG2)}`
  }
  return mode === 'metric' ? formatMetricMass(seg.baseG) : formatImperialMass(seg.baseG)
}

/** Display line in the requested unit system. `scaleFactor` scales mass/volume (e.g. servings), not temps or pan lines. */
export function formatIngredientLine(raw: string, mode: UnitMode, scaleFactor = 1): string {
  if (shouldSkipUnitConversion(raw)) return raw
  const segs = scaleSegments(findAllQuantityUnits(raw), scaleFactor)
  if (segs.length === 0) return raw
  let result = ''
  let cursor = 0
  for (const seg of segs) {
    result += raw.slice(cursor, seg.start)
    result += formatSegment(mode, seg)
    cursor = seg.end
  }
  result += raw.slice(cursor)
  return result
}

/** Other measurement string for tooltips (opposite of `mode`). */
export function alternateIngredientLine(raw: string, mode: UnitMode, scaleFactor = 1): string {
  if (shouldSkipUnitConversion(raw)) return raw
  const segs = scaleSegments(findAllQuantityUnits(raw), scaleFactor)
  if (segs.length === 0) return raw
  const other: UnitMode = mode === 'metric' ? 'imperial' : 'metric'
  let result = ''
  let cursor = 0
  for (const seg of segs) {
    result += raw.slice(cursor, seg.start)
    result += formatSegment(other, seg)
    cursor = seg.end
  }
  result += raw.slice(cursor)
  return result
}

/** Hover: alternate units, or note when pan sizes are skipped. */
export function ingredientTooltip(raw: string, mode: UnitMode, scaleFactor = 1): string | undefined {
  if (shouldSkipUnitConversion(raw)) {
    return 'Pan or tool size — not converted'
  }
  const primary = formatIngredientLine(raw, mode, scaleFactor)
  const alt = alternateIngredientLine(raw, mode, scaleFactor)
  if (primary === alt) return undefined
  return alt
}
