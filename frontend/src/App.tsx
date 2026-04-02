import { useEffect, useMemo, useRef, useState } from 'react'
import { buildTimeIso, buildTimeLabel, appVersion } from './buildInfo'
import {
  formatIngredientLine,
  ingredientTooltip,
  type UnitMode,
} from './ingredientUnits'
import { splitStepIntoChunks } from './splitStepText'
import {
  FDA_LABEL_BY_ID,
  NUTRIENT_ROW_ORDER,
  NUTRIENT_UNIT_BY_ID,
  SUB_NUTRIENT_IDS,
  caloriesPercentDailyValue,
  formatPercentDv,
  percentDailyValue,
} from './nutritionFacts'
import { exportRecipeCardPdf } from './recipeCardPdf'
import {
  isBookmarkedForUrl,
  loadBookmarks,
  persistBookmarks,
  removeBookmarkByUrl,
  upsertBookmark,
  type BookmarkEntry,
} from './bookmarkHistory'
import {
  clearPersistedRecipeHistory,
  loadRecipeHistory,
  normalizeRecipeUrl,
  persistRecipeHistory,
  upsertRecipeHistory,
  type RecipeHistoryEntry,
} from './recipeHistory'
import './App.css'

type ApiHealth = { status: string; database?: string }

type RecipePreview = {
  schemaVersion: number
  title: string
  description: string | null
  /** Extra text from page (e.g. Notes) — used to resolve “see notes” for nutrition. */
  notes?: string | null
  ingredients: { order: number; raw: string }[]
  steps: { order: number; text: string }[]
  servings: number | null
  prepTime: string | null
  cookTime: string | null
  totalTime: string | null
  imageUrl: string | null
  source: { kind: string; canonicalUrl: string; displayName?: string }
}

type ExtractResponse = {
  method: string
  warnings: string[]
  recipe: RecipePreview
}

type CalorieBreakdownRow = { ingredient: string; calories: number | null }

type IngredientNutrientBreakdownRow = {
  display_name: string
  grams_full_recipe: number | null
  calories: number | null
  nutrients: { id: string; label: string; quantity: number; unit: string }[]
}

type NutritionKeysStatus = { hasUsdaApiKey: boolean; hasEdamam: boolean }

type NutritionResponse = {
  ok: boolean
  source: string | null
  message: string | null
  calories: number | null
  nutrients: { id: string; label: string; quantity: number; unit: string }[]
  note?: string | null
  calorie_breakdown?: CalorieBreakdownRow[]
  ingredient_nutrient_breakdown?: IngredientNutrientBreakdownRow[]
  /** When true (default), values are modeled estimates — show * / label styling. */
  estimated?: boolean
}

async function readError(res: Response): Promise<string> {
  try {
    const data: unknown = await res.json()
    if (data && typeof data === 'object' && 'detail' in data) {
      const d = (data as { detail: unknown }).detail
      if (typeof d === 'string') return d
      if (Array.isArray(d)) return JSON.stringify(d)
    }
  } catch {
    /* ignore */
  }
  return (await res.text()) || res.statusText
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: init?.credentials ?? 'include',
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json() as Promise<T>
}

const NOTES_WARNING_FILTER_PREFIX = 'Notes text was captured from the page'

/** ~2 digit-wide field; grows in `ch` up to 5 when the value is longer. */
function servingsInputWidthCh(n: number): number {
  const len = String(n).length
  return Math.min(5, Math.max(2, len))
}

function sourceLabel(url: string, displayName?: string): string {
  if (url.trim() === '') {
    return displayName && displayName.length > 0 ? displayName : 'Pasted text'
  }
  try {
    const host = new URL(url).hostname.replace(/^www\./, '')
    return displayName && displayName.length > 0 ? displayName : host
  } catch {
    return displayName ?? url
  }
}

function BookmarkIcon({ filled }: { filled: boolean }) {
  return (
    <svg
      className="bookmark-icon-svg"
      viewBox="0 0 24 28"
      width="22"
      height="22"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M 6 3 H 18 Q 19 3 19 4 V 24 L 12 18.5 L 5 24 V 4 Q 5 3 6 3 Z"
        fill={filled ? 'currentColor' : 'none'}
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function App() {
  const [health, setHealth] = useState<ApiHealth | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)

  const [url, setUrl] = useState('')
  const [extracting, setExtracting] = useState(false)
  const [extractError, setExtractError] = useState<string | null>(null)
  const [extractResult, setExtractResult] = useState<ExtractResponse | null>(null)

  const [unitMode, setUnitMode] = useState<UnitMode>('imperial')
  const [checkedOrders, setCheckedOrders] = useState<Record<number, boolean>>({})
  /** Servings you’re making; scales ingredient amounts vs. the recipe’s stated yield. */
  const [servings, setServings] = useState(4)

  const [nutrition, setNutrition] = useState<NutritionResponse | null>(null)
  const [nutritionLoading, setNutritionLoading] = useState(false)
  const [nutritionKeysStatus, setNutritionKeysStatus] = useState<NutritionKeysStatus | null>(null)
  const [nutritionKeysStatusLoading, setNutritionKeysStatusLoading] = useState(false)
  const [nutritionKeysEpoch, setNutritionKeysEpoch] = useState(0)
  const [nutritionKeyModalOpen, setNutritionKeyModalOpen] = useState(false)
  const [draftUsda, setDraftUsda] = useState('')
  const [draftEdamamId, setDraftEdamamId] = useState('')
  const [draftEdamamKey, setDraftEdamamKey] = useState('')
  const [nutritionKeyModalError, setNutritionKeyModalError] = useState<string | null>(null)
  const [nutritionKeySaving, setNutritionKeySaving] = useState(false)
  const [calorieBreakdownOpen, setCalorieBreakdownOpen] = useState(false)
  const [expandedIngredientRows, setExpandedIngredientRows] = useState<Set<number>>(() => new Set())

  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [exportingPdf, setExportingPdf] = useState(false)
  const [recipeHistory, setRecipeHistory] = useState<RecipeHistoryEntry[]>(() => loadRecipeHistory())
  const [bookmarks, setBookmarks] = useState<BookmarkEntry[]>(() => loadBookmarks())
  const [bookmarksModalOpen, setBookmarksModalOpen] = useState(false)
  const [recipeMenuOpen, setRecipeMenuOpen] = useState(false)
  const recipeMenuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    fetchJson<ApiHealth>('/api/health')
      .then((data) => {
        if (!cancelled) setHealth(data)
      })
      .catch((e: unknown) => {
        if (!cancelled) setHealthError(e instanceof Error ? e.message : 'Request failed')
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    setCheckedOrders({})
  }, [extractResult])

  const recipeBaseServings = useMemo(() => {
    if (!extractResult) return 4
    const s = extractResult.recipe.servings
    return s != null && s > 0 && Number.isFinite(s) ? s : 4
  }, [extractResult])

  useEffect(() => {
    if (!extractResult) return
    setServings(recipeBaseServings)
  }, [extractResult, recipeBaseServings])

  const servingsIsEstimated =
    extractResult != null &&
    (extractResult.recipe.servings == null || extractResult.recipe.servings <= 0)

  const visibleWarnings = useMemo(() => {
    if (!extractResult) return []
    return extractResult.warnings.filter((w) => !w.startsWith(NOTES_WARNING_FILTER_PREFIX))
  }, [extractResult])

  const currentBookmarkUrl = useMemo(() => {
    if (!extractResult) return ''
    const fromSource = extractResult.recipe.source.canonicalUrl.trim()
    if (fromSource) return normalizeRecipeUrl(fromSource)
    return normalizeRecipeUrl(url.trim())
  }, [extractResult, url])

  const currentBookmarked = useMemo(
    () => isBookmarkedForUrl(bookmarks, currentBookmarkUrl),
    [bookmarks, currentBookmarkUrl],
  )

  const scaleFactor = servings / Math.max(recipeBaseServings, 0.25)

  /** Full-recipe nutrition ÷ stated yield (not affected by scaling ingredient amounts). */
  const perServingNutritionScale = useMemo(
    () => 1 / Math.max(recipeBaseServings, 0.25),
    [recipeBaseServings],
  )

  useEffect(() => {
    if (!nutritionKeyModalOpen) return
    setDraftUsda('')
    setDraftEdamamId('')
    setDraftEdamamKey('')
    setNutritionKeyModalError(null)
    let cancelled = false
    setNutritionKeysStatusLoading(true)
    fetchJson<NutritionKeysStatus>('/api/nutrition/keys-status')
      .then((s) => {
        if (!cancelled) setNutritionKeysStatus(s)
      })
      .catch(() => {
        if (!cancelled) setNutritionKeysStatus({ hasUsdaApiKey: false, hasEdamam: false })
      })
      .finally(() => {
        if (!cancelled) setNutritionKeysStatusLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [nutritionKeyModalOpen])

  useEffect(() => {
    if (!extractResult?.recipe.ingredients.length) {
      setNutrition(null)
      return
    }
    let cancelled = false
    setNutritionLoading(true)
    setNutrition(null)
    fetchJson<NutritionResponse>('/api/nutrition', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: extractResult.recipe.title,
        ingredients: extractResult.recipe.ingredients.map((i) => i.raw),
        notes: extractResult.recipe.notes ?? null,
        description: extractResult.recipe.description ?? null,
      }),
    })
      .then((data) => {
        if (!cancelled) setNutrition(data)
      })
      .catch(() => {
        if (!cancelled)
          setNutrition({
            ok: false,
            source: null,
            message: 'Could not load nutrition.',
            calories: null,
            nutrients: [],
            calorie_breakdown: [],
            ingredient_nutrient_breakdown: [],
          })
      })
      .finally(() => {
        if (!cancelled) setNutritionLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [extractResult, nutritionKeysEpoch])

  useEffect(() => {
    setCalorieBreakdownOpen(false)
    setExpandedIngredientRows(new Set())
  }, [nutrition])

  useEffect(() => {
    if (!recipeMenuOpen) return
    function onPointerDown(ev: PointerEvent) {
      const el = recipeMenuRef.current
      if (!el || el.contains(ev.target as Node)) return
      setRecipeMenuOpen(false)
    }
    window.addEventListener('pointerdown', onPointerDown)
    return () => window.removeEventListener('pointerdown', onPointerDown)
  }, [recipeMenuOpen])

  useEffect(() => {
    if (!recipeMenuOpen && !bookmarksModalOpen && !nutritionKeyModalOpen) return
    function onKeyDown(ev: KeyboardEvent) {
      if (ev.key === 'Escape') {
        setRecipeMenuOpen(false)
        setBookmarksModalOpen(false)
        setNutritionKeyModalOpen(false)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [recipeMenuOpen, bookmarksModalOpen, nutritionKeyModalOpen])

  const ingredientRows = useMemo(() => {
    if (!extractResult) return []
    return extractResult.recipe.ingredients.map((ing) => ({
      order: ing.order,
      raw: ing.raw,
      display: formatIngredientLine(ing.raw, unitMode, scaleFactor),
      hoverTitle: ingredientTooltip(ing.raw, unitMode, scaleFactor),
    }))
  }, [extractResult, unitMode, scaleFactor])

  const stepRows = useMemo(() => {
    if (!extractResult) return []
    return extractResult.recipe.steps.map((st) => ({
      order: st.order,
      chunks: splitStepIntoChunks(st.text).map((chunk) => ({
        display: formatIngredientLine(chunk, unitMode, scaleFactor),
        hoverTitle: ingredientTooltip(chunk, unitMode, scaleFactor),
      })),
    }))
  }, [extractResult, unitMode, scaleFactor])

  function toggleIngredient(order: number) {
    setCheckedOrders((prev) => ({ ...prev, [order]: !prev[order] }))
  }

  async function runExtractFromUrl(rawUrl: string) {
    const trimmed = rawUrl.trim()
    if (!trimmed) {
      setExtractError('Enter a recipe URL.')
      return
    }
    setExtractError(null)
    setSaveMessage(null)
    setExtractResult(null)
    setExtracting(true)
    try {
      const data = await fetchJson<ExtractResponse>('/api/extract', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: trimmed }),
      })
      setExtractResult(data)
      const canonical = normalizeRecipeUrl(trimmed)
      setUrl(canonical)
      setRecipeHistory((prev) => {
        const next = upsertRecipeHistory(prev, trimmed, data.recipe.title)
        persistRecipeHistory(next)
        return next
      })
    } catch (err) {
      setExtractError(err instanceof Error ? err.message : 'Extraction failed')
    } finally {
      setExtracting(false)
    }
  }

  /** Extension / deep link: `/?extract=<encoded-url>` → extract once, then strip query (avoids duplicate runs). */
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const raw = params.get('extract')
    if (raw == null || raw.trim() === '') return

    let decoded = raw.trim()
    try {
      decoded = decodeURIComponent(decoded)
    } catch {
      /* keep trimmed raw */
    }

    params.delete('extract')
    const qs = params.toString()
    const next =
      window.location.pathname + (qs ? `?${qs}` : '') + (window.location.hash || '')
    window.history.replaceState(null, '', next)

    void runExtractFromUrl(decoded)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one-shot; param removed before remount
  }, [])

  function handleExtract(e: React.FormEvent) {
    e.preventDefault()
    void runExtractFromUrl(url.trim())
  }

  function clearExtractionHistory() {
    clearPersistedRecipeHistory()
    setRecipeHistory([])
  }

  function removeExtractionHistoryEntry(entryUrl: string) {
    const norm = normalizeRecipeUrl(entryUrl)
    setRecipeHistory((prev) => {
      const next = prev.filter((e) => normalizeRecipeUrl(e.url) !== norm)
      persistRecipeHistory(next)
      return next
    })
  }

  function toggleBookmark() {
    if (!extractResult || !currentBookmarkUrl) return
    const title = extractResult.recipe.title
    setBookmarks((prev) => {
      const next = currentBookmarked
        ? removeBookmarkByUrl(prev, currentBookmarkUrl)
        : upsertBookmark(prev, currentBookmarkUrl, title)
      persistBookmarks(next)
      return next
    })
  }

  const canSaveNutritionDraft =
    draftUsda.trim().length > 0 ||
    (draftEdamamId.trim().length > 0 && draftEdamamKey.trim().length > 0)

  async function saveNutritionKeysFromModal() {
    const u = draftUsda.trim()
    const ei = draftEdamamId.trim()
    const ek = draftEdamamKey.trim()
    if (!u && (!ei || !ek)) return
    setNutritionKeySaving(true)
    setNutritionKeyModalError(null)
    try {
      const validateBody: Record<string, string> = {}
      if (u) validateBody.usdaApiKey = u
      if (ei && ek) {
        validateBody.edamamAppId = ei
        validateBody.edamamAppKey = ek
      }
      const res = await fetchJson<{ ok: boolean; message: string | null }>('/api/nutrition/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(validateBody),
      })
      if (!res.ok) {
        setNutritionKeyModalError(res.message ?? 'Validation failed.')
        return
      }
      const st = await fetchJson<NutritionKeysStatus>('/api/nutrition/keys-status')
      setNutritionKeysStatus(st)
      setNutritionKeysEpoch((e) => e + 1)
      setDraftUsda('')
      setDraftEdamamId('')
      setDraftEdamamKey('')
    } catch (err) {
      setNutritionKeyModalError(err instanceof Error ? err.message : 'Request failed.')
    } finally {
      setNutritionKeySaving(false)
    }
  }

  async function removeStoredUsdaKey() {
    try {
      await fetchJson<{ ok: boolean }>(
        `/api/nutrition/keys?scope=${encodeURIComponent('usda')}`,
        { method: 'DELETE' },
      )
      setNutritionKeysStatus(await fetchJson<NutritionKeysStatus>('/api/nutrition/keys-status'))
      setNutritionKeysEpoch((e) => e + 1)
    } catch {
      /* ignore */
    }
  }

  async function removeStoredEdamamKeys() {
    try {
      await fetchJson<{ ok: boolean }>(
        `/api/nutrition/keys?scope=${encodeURIComponent('edamam')}`,
        { method: 'DELETE' },
      )
      setNutritionKeysStatus(await fetchJson<NutritionKeysStatus>('/api/nutrition/keys-status'))
      setNutritionKeysEpoch((e) => e + 1)
    } catch {
      /* ignore */
    }
  }

  async function handleExportPdf() {
    if (!extractResult) return
    setExportingPdf(true)
    try {
      const r = extractResult.recipe
      const url = r.source.canonicalUrl.trim()
      const sourceLine =
        url.length > 0
          ? `Source: ${sourceLabel(url, r.source.displayName)}`
          : r.source.displayName != null && r.source.displayName.length > 0
            ? `Source: ${r.source.displayName}`
            : undefined

      await exportRecipeCardPdf({
        recipeTitle: r.title,
        servesCount: servings,
        prepTime: r.prepTime,
        totalTime: r.totalTime,
        description: r.description,
        ingredients: ingredientRows.map((row) => row.display),
        steps: stepRows.map((sr) => sr.chunks.map((c) => c.display).join(' ')),
        notes: r.notes ?? null,
        sourceLine,
      })
    } catch (err) {
      console.error(err)
    } finally {
      setExportingPdf(false)
    }
  }

  async function handleSave() {
    if (!extractResult) return
    setSaveMessage(null)
    setSaving(true)
    const r = extractResult.recipe
    try {
      await fetchJson('/api/recipes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: r.title,
          description: r.description,
          ingredients: r.ingredients,
          steps: r.steps,
          servings: r.servings,
          prepTime: r.prepTime,
          cookTime: r.cookTime,
          totalTime: r.totalTime,
          imageUrl: r.imageUrl,
          source: r.source,
        }),
      })
      setSaveMessage('Saved to your library.')
    } catch (err) {
      setSaveMessage(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="app">
      <header className="header">
        <h1 className="title">Recipe Extractor</h1>
        <p className="tagline">
          Enter a recipe URL — we read JSON-LD and page content when available (including notes from the
          recipe card).
        </p>
      </header>

      <section className="panel" aria-live="polite">
        <h2 className="panel-title">API status</h2>
        {healthError != null && <p className="status error">Backend: {healthError}</p>}
        {healthError == null && health == null && <p className="status muted">Checking…</p>}
        {health != null && (
          <p className="status ok">
            {health.status}
            {health.database != null ? ` · database ${health.database}` : ''}
          </p>
        )}
        <p className="hint">
          <a href="/docs">OpenAPI docs</a>
        </p>
      </section>

      <section className="panel extract-panel">
        <h2 className="panel-title">Add a recipe</h2>
        <div className="extract-panel-row">
          <div className="extract-layout">
            <div className="extract-col extract-col-url">
              <h3 className="extract-col-heading">Extract from URL</h3>
              <p className="extract-col-hint muted small">
                Fetches the page and uses structured data when available. Notes from the recipe card may be
                merged automatically.
              </p>
              <form className="extract-form-stack" onSubmit={handleExtract}>
                <label className="sr-only" htmlFor="recipe-url">
                  Recipe URL
                </label>
                <input
                  id="recipe-url"
                  className="url-input"
                  type="url"
                  inputMode="url"
                  placeholder="https://example.com/some-recipe"
                  value={url}
                  onChange={(ev) => setUrl(ev.target.value)}
                  autoComplete="url"
                />
                <button className="btn primary" type="submit" disabled={extracting}>
                  {extracting ? 'Working…' : 'Extract'}
                </button>
              </form>
            </div>
          </div>
          <aside className="recipe-history-aside" aria-label="Extraction history">
            <div className="recipe-history-header">
              <h3 className="recipe-history-heading">Extraction History</h3>
              <button
                type="button"
                className="recipe-history-clear"
                disabled={recipeHistory.length === 0}
                onClick={clearExtractionHistory}
              >
                Clear
              </button>
            </div>
            <ul className="recipe-history-list" aria-live="polite">
              {recipeHistory.map((entry) => (
                <li key={entry.url} className="recipe-history-row">
                  <button
                    type="button"
                    className="recipe-history-item"
                    disabled={extracting}
                    title={entry.url}
                    onClick={() => void runExtractFromUrl(entry.url)}
                  >
                    <span className="recipe-history-title">{entry.title}</span>
                    <span className="recipe-history-url">{entry.url}</span>
                  </button>
                  <button
                    type="button"
                    className="recipe-history-remove"
                    aria-label={`Remove ${entry.title} from history`}
                    title="Remove from history"
                    onClick={() => removeExtractionHistoryEntry(entry.url)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          </aside>
        </div>
        {extractError != null && <p className="status error extract-error-shared">{extractError}</p>}
      </section>

      {extractResult != null && (
        <section className="panel result-panel">
          <div className="result-head">
            <div className="result-head-main">
              <h2 className="panel-title result-title">{extractResult.recipe.title}</h2>
              <span className="method-badge">{extractResult.method}</span>
            </div>
            <div className="result-head-actions" ref={recipeMenuRef}>
              <button
                type="button"
                className="btn secondary result-bookmark-btn"
                onClick={() => toggleBookmark()}
                disabled={!currentBookmarkUrl}
                title={
                  !currentBookmarkUrl
                    ? 'No URL to bookmark for this recipe'
                    : currentBookmarked
                      ? 'Remove bookmark'
                      : 'Bookmark this recipe'
                }
                aria-label={currentBookmarked ? 'Remove bookmark' : 'Bookmark recipe'}
                aria-pressed={currentBookmarked}
              >
                <BookmarkIcon filled={currentBookmarked} />
              </button>
              <div className="result-menu-wrap">
                <button
                  type="button"
                  className="btn secondary result-menu-trigger"
                  aria-haspopup="menu"
                  aria-expanded={recipeMenuOpen}
                  onClick={() => setRecipeMenuOpen((o) => !o)}
                  title="More actions"
                  aria-label="Recipe actions"
                >
                  ⋯
                </button>
                {recipeMenuOpen && (
                  <ul className="result-menu-dropdown" role="menu">
                    <li role="none">
                      <button
                        type="button"
                        role="menuitem"
                        className="result-menu-item"
                        onClick={() => {
                          setBookmarksModalOpen(true)
                          setRecipeMenuOpen(false)
                        }}
                      >
                        View bookmarks
                      </button>
                    </li>
                    <li role="none">
                      <button
                        type="button"
                        role="menuitem"
                        className="result-menu-item"
                        disabled={exportingPdf}
                        onClick={() => {
                          setRecipeMenuOpen(false)
                          void handleExportPdf()
                        }}
                      >
                        {exportingPdf ? 'Preparing PDF…' : 'Export as PDF'}
                      </button>
                    </li>
                  </ul>
                )}
              </div>
            </div>
          </div>
          {visibleWarnings.length > 0 && (
            <ul className="warnings">
              {visibleWarnings.map((w, i) => (
                <li key={`${i}-${w.slice(0, 48)}`}>{w}</li>
              ))}
            </ul>
          )}
          {extractResult.recipe.description != null && (
            <p className="description">{extractResult.recipe.description}</p>
          )}

          <div className="servings-bar">
            <label className="servings-inline" htmlFor="servings-input">
              <input
                id="servings-input"
                className="servings-input servings-input-inline"
                type="number"
                min={0.25}
                step={0.25}
                inputMode="decimal"
                style={{ width: `${servingsInputWidthCh(servings)}ch` }}
                value={servings}
                onChange={(ev) => {
                  const v = parseFloat(ev.target.value)
                  if (Number.isFinite(v) && v > 0) setServings(v)
                }}
                aria-describedby={servingsIsEstimated ? 'servings-estimated-hint' : undefined}
              />
              <span className="servings-suffix">Servings</span>
            </label>
            {servingsIsEstimated && (
              <span id="servings-estimated-hint" className="servings-hint">
                Recipe yield not in source — using 4 servings as the baseline for scaling and nutrition.
              </span>
            )}
          </div>

          <div className="recipe-toolbar">
            <span className="unit-label">Units</span>
            <div className="unit-toggle" role="group" aria-label="Measurement units">
              <button
                type="button"
                className={`unit-btn ${unitMode === 'metric' ? 'active' : ''}`}
                onClick={() => setUnitMode('metric')}
              >
                Metric
              </button>
              <button
                type="button"
                className={`unit-btn ${unitMode === 'imperial' ? 'active' : ''}`}
                onClick={() => setUnitMode('imperial')}
              >
                Imperial
              </button>
            </div>
          </div>

          <div className="recipe-grid">
            <div className="recipe-col nutrition-col">
              <div className="nutrition-col-head">
                <h3 className="subhead nutrition-col-title">Nutrition</h3>
                <button
                  type="button"
                  className="btn secondary nutrition-api-key-btn"
                  onClick={() => setNutritionKeyModalOpen(true)}
                >
                  Update API Key
                </button>
              </div>
              <p className="nutrition-source muted small">
                {nutrition?.ok && nutrition.source === 'usda' ? (
                  <>
                    <a href="https://fdc.nal.usda.gov/" target="_blank" rel="noreferrer">
                      USDA FoodData Central
                    </a>{' '}
                    (free API; each ingredient is matched to the top search result, per-100g data — approximate).
                  </>
                ) : nutrition?.ok && nutrition.source === 'edamam' ? (
                  <>
                    <a
                      href="https://developer.edamam.com/edamam-nutrition-api"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Edamam
                    </a>{' '}
                    (free developer tier; full-recipe analysis).
                  </>
                ) : (
                  <>
                    Set <code className="inline-code">USDA_API_KEY</code> (recommended, free) or Edamam keys on
                    the backend, or use <strong>Update API Key</strong> to store keys in this browser only.
                  </>
                )}{' '}
                Values are <strong>per 1 serving</strong> of the full recipe (full recipe ÷ stated yield).
                Ingredient lines scale when you change Servings above; nutrition per serving does not.
              </p>
              {nutritionLoading && extractResult != null && extractResult.recipe.ingredients.length > 0 && (
                <div
                  className="nf-panel nf-panel-skeleton"
                  aria-busy="true"
                  aria-live="polite"
                  aria-label="Loading nutrition facts"
                >
                  <div className="nf-label-inner">
                    <p className="nf-skeleton-banner">
                      <span className="nf-skeleton-pulse">Loading…</span> Resolving each ingredient (USDA
                      lookup + portions). First run is slower; cache speeds repeat matches, but the server
                      still finishes all lines in one request.
                    </p>
                    <h4 className="nf-heading">Nutrition Facts</h4>
                    <p className="nf-serving-label">Amount per serving</p>
                    <p className="nf-serving-meta">
                      Full recipe ÷ {recipeBaseServings} serving
                      {recipeBaseServings === 1 ? '' : 's'}
                      {servingsIsEstimated ? ' (yield not in recipe — assumed 4)' : ''}.
                    </p>
                    <div className="nf-bar nf-bar-thick" aria-hidden="true" />
                    <div className="nf-calories-line">
                      <span className="nf-calories-word">Calories</span>
                      <span className="nf-calories-num nf-skeleton-pulse" title="Pending">
                        —
                      </span>
                    </div>
                    <p className="nf-cal-dv">
                      % Daily Value for calories:{' '}
                      <strong className="nf-skeleton-pulse">—</strong>
                    </p>
                    <div className="nf-bar" aria-hidden="true" />
                    <div className="nf-dv-column-head">
                      <span />
                      <span>% Daily Value*</span>
                    </div>
                    {NUTRIENT_ROW_ORDER.map((nid) => {
                      const label = FDA_LABEL_BY_ID[nid] ?? nid
                      const unit = NUTRIENT_UNIT_BY_ID[nid] ?? ''
                      const isSub = SUB_NUTRIENT_IDS.has(nid)
                      return (
                        <div
                          key={`sk-${nid}`}
                          className={`nf-nutrient-row ${isSub ? 'nf-nutrient-sub' : ''}`}
                        >
                          <span className="nf-nutrient-left">
                            {isSub ? <span>{label}</span> : <strong>{label}</strong>}{' '}
                            <span className="nf-nutrient-amt">
                              <span className="nf-skeleton-pulse">—</span>
                              {unit ? ` ${unit}` : ''}
                            </span>
                          </span>
                          <span className="nf-nutrient-dv nf-skeleton-pulse">—</span>
                        </div>
                      )
                    })}
                    <div className="nf-bar nf-bar-thick" aria-hidden="true" />
                    <p className="nf-legal nf-skeleton-legal">
                      * Values appear when analysis completes. % Daily Value is based on a 2,000-calorie diet.
                    </p>
                  </div>
                </div>
              )}
              {!nutritionLoading && nutrition != null && !nutrition.ok && (
                <p className="nutrition-msg muted">{nutrition.message}</p>
              )}
              {!nutritionLoading && nutrition != null && nutrition.ok && (
                <div className="nf-panel nf-panel-enter">
                  <div className="nf-label-inner">
                    {(nutrition.estimated !== false || nutrition.source != null) && (
                      <p className="nf-estimate-banner">
                        <span className="nf-asterisk" aria-hidden="true">
                          *
                        </span>{' '}
                        <strong>Estimated</strong> — amounts come from database matches and parsed recipe text,
                        not laboratory analysis.
                      </p>
                    )}
                    <h4 className="nf-heading">Nutrition Facts</h4>
                    <p className="nf-serving-label">Amount per serving</p>
                    <p className="nf-serving-meta">
                      Full recipe ÷ {recipeBaseServings} serving
                      {recipeBaseServings === 1 ? '' : 's'}
                      {servingsIsEstimated ? ' (yield not in recipe — assumed 4)' : ''}. Nutrition per serving is
                      for the full batch; ingredient amounts follow Servings above.
                    </p>
                    <div className="nf-bar nf-bar-thick" aria-hidden="true" />
                    {nutrition.calories != null && (
                      <>
                        <div className="nf-calories-line">
                          <span className="nf-calories-word">Calories</span>
                          <span className="nf-calories-num">
                            {Math.round(nutrition.calories * perServingNutritionScale)}
                          </span>
                        </div>
                        <p className="nf-cal-dv">
                          % Daily Value for calories:{' '}
                          <strong>
                            {formatPercentDv(
                              caloriesPercentDailyValue(nutrition.calories * perServingNutritionScale),
                            )}
                          </strong>
                        </p>
                      </>
                    )}
                    {nutrition.nutrients.length > 0 && (
                      <>
                        <div className="nf-bar" aria-hidden="true" />
                        <div className="nf-dv-column-head">
                          <span />
                          <span>% Daily Value*</span>
                        </div>
                      </>
                    )}
                    {nutrition.nutrients.map((n) => {
                      const scaled = n.quantity * perServingNutritionScale
                      const pct = percentDailyValue(n.id, scaled)
                      const displayName = FDA_LABEL_BY_ID[n.id] ?? n.label
                      const isSub = SUB_NUTRIENT_IDS.has(n.id)
                      const amountStr =
                        n.unit === 'kcal'
                          ? String(Math.round(scaled))
                          : roundNutrient(scaled)
                      return (
                        <div
                          key={n.id}
                          className={`nf-nutrient-row ${isSub ? 'nf-nutrient-sub' : ''}`}
                        >
                          <span className="nf-nutrient-left">
                            {isSub ? (
                              <span>{displayName}</span>
                            ) : (
                              <strong>{displayName}</strong>
                            )}{' '}
                            <span className="nf-nutrient-amt">
                              {amountStr}
                              {n.unit ? ` ${n.unit}` : ''}
                            </span>
                          </span>
                          <span className="nf-nutrient-dv">{formatPercentDv(pct)}</span>
                        </div>
                      )
                    })}
                    <div className="nf-bar nf-bar-thick" aria-hidden="true" />
                    <p className="nf-legal">
                      * The % Daily Value (DV) tells you how much a nutrient in a serving of food contributes
                      to a daily diet. <strong>2,000 calories</strong> a day is used for general nutrition
                      advice.
                    </p>
                    {nutrition.note != null && nutrition.note.length > 0 && (
                      <p className="nf-backend-note">{nutrition.note}</p>
                    )}
                  </div>
                  {nutrition.calories != null &&
                    nutrition.calorie_breakdown != null &&
                    nutrition.calorie_breakdown.length > 0 &&
                    (nutrition.ingredient_nutrient_breakdown == null ||
                      nutrition.ingredient_nutrient_breakdown.length === 0) && (
                      <div className="nf-breakdown-wrap">
                        <div className="nf-breakdown-head">
                          <span className="nf-breakdown-title">
                            Calories by ingredient <span className="nf-asterisk">*</span>
                          </span>
                          <button
                            type="button"
                            className="nf-calorie-help"
                            aria-label={
                              calorieBreakdownOpen
                                ? 'Hide calories per ingredient'
                                : 'Show calories per ingredient'
                            }
                            aria-expanded={calorieBreakdownOpen}
                            aria-controls="nutrition-calorie-breakdown-legacy"
                            title="Toggle breakdown"
                            onClick={() => setCalorieBreakdownOpen((o) => !o)}
                          >
                            ?
                          </button>
                        </div>
                        <div
                          className="nutrition-calorie-breakdown nf-breakdown-panel"
                          id="nutrition-calorie-breakdown-legacy"
                          hidden={!calorieBreakdownOpen}
                        >
                          <p className="nutrition-breakdown-heading">
                            Estimated kcal per line, per serving (full recipe ÷ stated yield). Long lines may be
                            split (e.g. rice + bread).
                          </p>
                          <ul className="nutrition-breakdown-list">
                            {nutrition.calorie_breakdown.map((row, i) => (
                              <li key={`${i}-${row.ingredient.slice(0, 40)}`}>
                                <span className="nutrition-breakdown-ingredient">{row.ingredient}</span>
                                <span className="nutrition-breakdown-kcal nf-kcal-est">
                                  {row.calories != null
                                    ? `${Math.round(row.calories * perServingNutritionScale)} kcal`
                                    : '—'}
                                  {row.calories != null && (
                                    <span className="nf-kcal-star" aria-hidden="true">
                                      *
                                    </span>
                                  )}
                                </span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    )}
                  {nutrition.ingredient_nutrient_breakdown != null &&
                    nutrition.ingredient_nutrient_breakdown.length > 0 && (
                      <div className="nf-breakdown-wrap nf-breakdown-wrap-wide">
                        <div className="nf-breakdown-head">
                          <span className="nf-breakdown-title">
                            Nutrients by ingredient <span className="nf-asterisk">*</span>
                          </span>
                          <button
                            type="button"
                            className="nf-calorie-help"
                            aria-label={
                              calorieBreakdownOpen
                                ? 'Hide nutrients per ingredient'
                                : 'Show nutrients per ingredient'
                            }
                            aria-expanded={calorieBreakdownOpen}
                            aria-controls="nutrition-ingredient-breakdown"
                            title="Toggle breakdown"
                            onClick={() => setCalorieBreakdownOpen((o) => !o)}
                          >
                            ?
                          </button>
                        </div>
                        <div
                          className="nutrition-calorie-breakdown nf-breakdown-panel"
                          id="nutrition-ingredient-breakdown"
                          hidden={!calorieBreakdownOpen}
                        >
                          <p className="nutrition-breakdown-heading">
                            Per 1 serving of the recipe (full batch ÷ stated yield). Expand a row for the full
                            label.
                            Names are shortened from the USDA match. Grams are estimated from the recipe line.
                          </p>
                          <ul className="nf-ing-breakdown-list">
                            {nutrition.ingredient_nutrient_breakdown.map((row, i) => {
                              const expanded = expandedIngredientRows.has(i)
                              const gramsPortion =
                                row.grams_full_recipe != null
                                  ? Math.round(row.grams_full_recipe * perServingNutritionScale)
                                  : null
                              const kcalPortion =
                                row.calories != null
                                  ? Math.round(row.calories * perServingNutritionScale)
                                  : null
                              return (
                                <li key={`${i}-${row.display_name.slice(0, 48)}`} className="nf-ing-row">
                                  <button
                                    type="button"
                                    className="nf-ing-row-toggle"
                                    aria-expanded={expanded}
                                    aria-controls={`nf-ing-detail-${i}`}
                                    id={`nf-ing-head-${i}`}
                                    onClick={() => {
                                      setExpandedIngredientRows((prev) => {
                                        const next = new Set(prev)
                                        if (next.has(i)) next.delete(i)
                                        else next.add(i)
                                        return next
                                      })
                                    }}
                                  >
                                    <span className="nf-ing-name">{row.display_name}</span>
                                    <span className="nf-ing-preview">
                                      {gramsPortion != null ? (
                                        <span className="nf-ing-grams">≈ {gramsPortion} g</span>
                                      ) : (
                                        <span className="nf-ing-grams">—</span>
                                      )}
                                      <span className="nf-ing-preview-sep" aria-hidden="true">
                                        ·
                                      </span>
                                      <span className="nf-ing-preview-kcal">
                                        {kcalPortion != null ? `${kcalPortion} kcal` : '—'}
                                        {kcalPortion != null && (
                                          <span className="nf-kcal-star" aria-hidden="true">
                                            *
                                          </span>
                                        )}
                                      </span>
                                    </span>
                                    <span className="nf-ing-chevron" aria-hidden="true">
                                      {expanded ? '▾' : '▸'}
                                    </span>
                                  </button>
                                  {expanded && (
                                    <div
                                      className="nf-ing-expanded"
                                      id={`nf-ing-detail-${i}`}
                                      role="region"
                                      aria-labelledby={`nf-ing-head-${i}`}
                                    >
                                      <p className="nf-ing-amt-note">
                                        {gramsPortion != null
                                          ? `≈ ${gramsPortion} g of this food in one portion of this recipe.`
                                          : 'Amount in grams not estimated for this line.'}
                                      </p>
                                      <div className="nf-ing-expanded-inner">
                                        {row.calories != null && (
                                          <div className="nf-ing-cal-row">
                                            <span className="nf-ing-cell-label">
                                              <strong>Calories</strong>{' '}
                                              <span className="nf-ing-amt-inline">
                                                {Math.round(row.calories * perServingNutritionScale)}
                                              </span>
                                            </span>
                                            <span className="nf-ing-cell-dv">
                                              {formatPercentDv(
                                                caloriesPercentDailyValue(
                                                  row.calories * perServingNutritionScale,
                                                ),
                                              )}
                                            </span>
                                          </div>
                                        )}
                                        {row.nutrients.length > 0 && (
                                          <>
                                            <div className="nf-ing-dv-head">
                                              <span />
                                              <span>% Daily Value*</span>
                                            </div>
                                            {row.nutrients.map((n) => {
                                              const scaled = n.quantity * perServingNutritionScale
                                              const pct = percentDailyValue(n.id, scaled)
                                              const displayName = FDA_LABEL_BY_ID[n.id] ?? n.label
                                              const isSub = SUB_NUTRIENT_IDS.has(n.id)
                                              const amountStr =
                                                n.unit === 'kcal'
                                                  ? String(Math.round(scaled))
                                                  : roundNutrient(scaled)
                                              return (
                                                <div
                                                  key={n.id}
                                                  className={`nf-ing-nut-row ${isSub ? 'nf-ing-nut-sub' : ''}`}
                                                >
                                                  <span className="nf-ing-cell-label">
                                                    {isSub ? (
                                                      <span>{displayName}</span>
                                                    ) : (
                                                      <strong>{displayName}</strong>
                                                    )}{' '}
                                                    <span className="nf-ing-amt-inline">
                                                      {amountStr}
                                                      {n.unit ? ` ${n.unit}` : ''}
                                                    </span>
                                                  </span>
                                                  <span className="nf-ing-cell-dv">
                                                    {formatPercentDv(pct)}
                                                  </span>
                                                </div>
                                              )
                                            })}
                                          </>
                                        )}
                                      </div>
                                    </div>
                                  )}
                                </li>
                              )
                            })}
                          </ul>
                        </div>
                      </div>
                    )}
                </div>
              )}
            </div>

            <div className="recipe-col ingredients-col">
              <h3 className="subhead">Ingredients</h3>
              {extractResult.recipe.ingredients.length === 0 ? (
                <p className="muted">None extracted.</p>
              ) : (
                <ul className="ingredient-checklist">
                  {ingredientRows.map((row) => (
                    <li key={row.order}>
                      <label className="ingredient-label">
                        <input
                          type="checkbox"
                          className="ingredient-check"
                          checked={!!checkedOrders[row.order]}
                          onChange={() => toggleIngredient(row.order)}
                          aria-label={`Got ${row.display}`}
                        />
                        <span
                          className={`ingredient-line ${checkedOrders[row.order] ? 'crossed' : ''} ${row.hoverTitle ? 'has-tip' : ''}`}
                          title={row.hoverTitle}
                        >
                          {row.display}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="recipe-col steps-col">
              <h3 className="subhead">Steps</h3>
              {extractResult.recipe.steps.length === 0 ? (
                <p className="muted">None extracted.</p>
              ) : (
                <ol className="step-list step-list-readable">
                  {stepRows.map((row) => (
                    <li key={row.order} className="step-item">
                      {row.chunks.map((chunk, idx) => (
                        <p
                          key={`${row.order}-${idx}`}
                          className={`step-chunk ${chunk.hoverTitle ? 'has-tip' : ''}`}
                          title={chunk.hoverTitle}
                        >
                          {chunk.display}
                        </p>
                      ))}
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </div>

          <div className="save-row">
            <button className="btn secondary" type="button" onClick={handleSave} disabled={saving}>
              {saving ? 'Saving…' : 'Save to library'}
            </button>
            {saveMessage != null && (
              <span className={saveMessage.startsWith('Saved') ? 'save-ok' : 'save-err'}>
                {saveMessage}
              </span>
            )}
          </div>

          {extractResult.recipe.notes != null && extractResult.recipe.notes.length > 0 && (
            <aside
              id="recipe-notes-footnote"
              className="recipe-notes-block recipe-notes-footnote"
              aria-label="Recipe notes footnote"
            >
              <h3 className="subhead recipe-notes-footnote-title">Notes</h3>
              <p className="recipe-notes-footnote-meta muted small">
                additional notes and instructions fetched during Extraction
              </p>
              <div className="recipe-notes-body">{extractResult.recipe.notes}</div>
            </aside>
          )}

          <footer className="extract-footer">
            <span className="extract-footer-label">Source:</span>{' '}
            {extractResult.recipe.source.canonicalUrl.trim() !== '' ? (
              <a
                className="extract-footer-link"
                href={extractResult.recipe.source.canonicalUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                {sourceLabel(
                  extractResult.recipe.source.canonicalUrl,
                  extractResult.recipe.source.displayName,
                )}
              </a>
            ) : (
              <span className="extract-footer-pasted">
                {sourceLabel('', extractResult.recipe.source.displayName)}
              </span>
            )}
          </footer>
        </section>
      )}

      {nutritionKeyModalOpen && (
        <div
          className="nutrition-keys-modal-backdrop"
          role="presentation"
          onClick={() => setNutritionKeyModalOpen(false)}
        >
          <div
            className="nutrition-keys-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="nutrition-keys-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="nutrition-keys-modal-head">
              <h2 id="nutrition-keys-modal-title" className="nutrition-keys-modal-title">
                Nutrition API keys
              </h2>
              <button
                type="button"
                className="btn secondary nutrition-keys-modal-close"
                onClick={() => setNutritionKeyModalOpen(false)}
              >
                Close
              </button>
            </div>
            <div className="nutrition-keys-modal-body">
              <p className="nutrition-keys-intro muted small">
                Keys are stored in <strong>HttpOnly cookies</strong> for this site (JavaScript cannot read them).
                Remove a key here to use the server <code className="inline-code">.env</code> again for that provider.
                USDA is used first when both are stored. Use HTTPS in production so cookies are marked{' '}
                <code className="inline-code">Secure</code>.
              </p>
              {nutritionKeysStatusLoading && (
                <p className="nutrition-keys-status-loading muted small">Loading saved keys…</p>
              )}
              {!nutritionKeysStatusLoading &&
                nutritionKeysStatus &&
                (nutritionKeysStatus.hasUsdaApiKey || nutritionKeysStatus.hasEdamam) && (
                  <div className="nutrition-keys-saved-block">
                    <p className="nutrition-keys-saved-label">Saved for this browser</p>
                    {nutritionKeysStatus.hasUsdaApiKey && (
                      <div className="nutrition-keys-saved-row">
                        <span>USDA API key</span>
                        <span className="nutrition-keys-mask" title="Stored key hidden">
                          ********
                        </span>
                        <button
                          type="button"
                          className="btn secondary nutrition-keys-remove"
                          onClick={() => void removeStoredUsdaKey()}
                        >
                          Remove
                        </button>
                      </div>
                    )}
                    {nutritionKeysStatus.hasEdamam && (
                      <div className="nutrition-keys-saved-row">
                        <span>Edamam</span>
                        <span className="nutrition-keys-mask" title="Stored credentials hidden">
                          ********
                        </span>
                        <button
                          type="button"
                          className="btn secondary nutrition-keys-remove"
                          onClick={() => void removeStoredEdamamKeys()}
                        >
                          Remove
                        </button>
                      </div>
                    )}
                  </div>
                )}
              <div className="nutrition-keys-field-group">
                <label className="nutrition-keys-label">
                  USDA API key{' '}
                  <a
                    href="https://fdc.nal.usda.gov/api-key-signup"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="nutrition-keys-doc-link"
                  >
                    Get a free key (FoodData Central)
                  </a>
                </label>
                <input
                  className="nutrition-keys-input"
                  type="password"
                  autoComplete="off"
                  value={draftUsda}
                  onChange={(e) => setDraftUsda(e.target.value)}
                  placeholder="Paste new key to validate and save"
                />
              </div>
              <div className="nutrition-keys-field-group">
                <label className="nutrition-keys-label">
                  Edamam App ID & App Key{' '}
                  <a
                    href="https://developer.edamam.com/edamam-nutrition-api"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="nutrition-keys-doc-link"
                  >
                    Edamam Nutrition API
                  </a>
                </label>
                <input
                  className="nutrition-keys-input"
                  type="text"
                  autoComplete="off"
                  value={draftEdamamId}
                  onChange={(e) => setDraftEdamamId(e.target.value)}
                  placeholder="App ID"
                />
                <input
                  className="nutrition-keys-input nutrition-keys-input-mt"
                  type="password"
                  autoComplete="off"
                  value={draftEdamamKey}
                  onChange={(e) => setDraftEdamamKey(e.target.value)}
                  placeholder="App Key"
                />
              </div>
              {nutritionKeyModalError != null && (
                <p className="nutrition-keys-error" role="alert">
                  {nutritionKeyModalError}
                </p>
              )}
              <div className="nutrition-keys-actions">
                <button
                  type="button"
                  className="btn secondary"
                  disabled={nutritionKeySaving}
                  onClick={() => setNutritionKeyModalOpen(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn primary"
                  disabled={!canSaveNutritionDraft || nutritionKeySaving}
                  onClick={() => void saveNutritionKeysFromModal()}
                >
                  {nutritionKeySaving ? 'Validating…' : 'Save'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {bookmarksModalOpen && (
        <div
          className="bookmarks-modal-backdrop"
          role="presentation"
          onClick={() => setBookmarksModalOpen(false)}
        >
          <div
            className="bookmarks-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="bookmarks-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="bookmarks-modal-head">
              <h2 id="bookmarks-modal-title" className="bookmarks-modal-title">
                Bookmarks
              </h2>
              <button
                type="button"
                className="btn secondary bookmarks-modal-close"
                onClick={() => setBookmarksModalOpen(false)}
              >
                Close
              </button>
            </div>
            <ul className="bookmarks-modal-list">
              {bookmarks.map((entry) => (
                <li key={entry.url}>
                  <button
                    type="button"
                    className="bookmarks-modal-row"
                    title={entry.url}
                    disabled={extracting}
                    onClick={() => {
                      void runExtractFromUrl(entry.url)
                      setBookmarksModalOpen(false)
                    }}
                  >
                    <span className="bookmarks-modal-row-title">{entry.title}</span>
                    <span className="bookmarks-modal-row-url">{entry.url}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <footer className="app-version-footer" title={`ISO: ${buildTimeIso()}`}>
        <span className="app-version-text">
          <span className="app-version-label">Frontend</span>{' '}
          <code className="app-version-code">v{appVersion()}</code>
          <span className="app-version-sep">·</span>
          <time dateTime={buildTimeIso()}>{buildTimeLabel(buildTimeIso())}</time>
        </span>
      </footer>
    </div>
  )
}

function roundNutrient(q: number): string {
  if (!Number.isFinite(q)) return '—'
  if (Math.abs(q) >= 100) return String(Math.round(q))
  if (Math.abs(q) >= 10) return q.toFixed(1)
  return q.toFixed(2)
}

export default App
