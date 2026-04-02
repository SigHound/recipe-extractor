import { normalizeRecipeUrl } from './recipeHistory'

export type BookmarkEntry = {
  url: string
  title: string
  savedAt: number
}

const STORAGE_KEY = 'recipe-extractor:v1:bookmarks'
const MAX_ENTRIES = 48

export function loadBookmarks(): BookmarkEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const data: unknown = JSON.parse(raw)
    if (!Array.isArray(data)) return []
    const out: BookmarkEntry[] = []
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

export function persistBookmarks(entries: BookmarkEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
  } catch {
    /* quota / private mode */
  }
}

/** Newest first; same normalized URL deduped. */
export function upsertBookmark(
  prev: BookmarkEntry[],
  rawUrl: string,
  title: string,
): BookmarkEntry[] {
  const norm = normalizeRecipeUrl(rawUrl)
  if (!norm) return prev
  const without = prev.filter((e) => normalizeRecipeUrl(e.url) !== norm)
  const next: BookmarkEntry = {
    url: norm,
    title: title.trim() || 'Recipe',
    savedAt: Date.now(),
  }
  return [next, ...without].slice(0, MAX_ENTRIES)
}

export function removeBookmarkByUrl(prev: BookmarkEntry[], rawUrl: string): BookmarkEntry[] {
  const norm = normalizeRecipeUrl(rawUrl)
  if (!norm) return prev
  return prev.filter((e) => normalizeRecipeUrl(e.url) !== norm)
}

export function isBookmarkedForUrl(entries: BookmarkEntry[], rawUrl: string): boolean {
  const norm = normalizeRecipeUrl(rawUrl)
  if (!norm) return false
  return entries.some((e) => normalizeRecipeUrl(e.url) === norm)
}
