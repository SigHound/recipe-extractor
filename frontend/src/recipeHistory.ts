/**
 * Persist recent recipe URLs in localStorage (survives rebuilds; browser-local only).
 */

export type RecipeHistoryEntry = {
  url: string
  title: string
  savedAt: number
}

const STORAGE_KEY = 'recipe-extractor:v1:history'
const MAX_ENTRIES = 48

/** Canonical URL for deduping (no hash; trailing slash trimmed on path). */
export function normalizeRecipeUrl(raw: string): string {
  const t = raw.trim()
  if (!t) return ''
  try {
    const u = new URL(t)
    u.hash = ''
    let path = u.pathname
    if (path.length > 1 && path.endsWith('/')) {
      u.pathname = path.slice(0, -1) || '/'
    }
    return u.href
  } catch {
    return t
  }
}

export function loadRecipeHistory(): RecipeHistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const data: unknown = JSON.parse(raw)
    if (!Array.isArray(data)) return []
    const out: RecipeHistoryEntry[] = []
    for (const item of data) {
      if (item != null && typeof item === 'object' && 'url' in item) {
        const o = item as Record<string, unknown>
        if (typeof o.url !== 'string') continue
        out.push({
          url: o.url,
          title: typeof o.title === 'string' && o.title.trim() ? o.title.trim() : 'Recipe',
          savedAt: typeof o.savedAt === 'number' ? o.savedAt : 0,
        })
      }
    }
    return out
  } catch {
    return []
  }
}

export function persistRecipeHistory(entries: RecipeHistoryEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
  } catch {
    /* quota / private mode */
  }
}

export function clearPersistedRecipeHistory(): void {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* ignore */
  }
}

/** Newest first; same normalized URL replaces older entry and moves to top. */
export function upsertRecipeHistory(
  prev: RecipeHistoryEntry[],
  rawUrl: string,
  title: string,
): RecipeHistoryEntry[] {
  const norm = normalizeRecipeUrl(rawUrl)
  if (!norm) return prev
  const without = prev.filter((e) => normalizeRecipeUrl(e.url) !== norm)
  const next: RecipeHistoryEntry = {
    url: norm,
    title: title.trim() || 'Recipe',
    savedAt: Date.now(),
  }
  return [next, ...without].slice(0, MAX_ENTRIES)
}
