import html2canvas from 'html2canvas'
import { jsPDF } from 'jspdf'
import { formatRecipeDuration } from './durationFormat'

export type RecipeCardExportInput = {
  /** Dish name (shown under the script “Recipe” header). */
  recipeTitle: string
  /** Numeric servings (header shows “SERVES: 8”, not “8 servings”). */
  servesCount: number
  prepTime: string | null
  totalTime: string | null
  description: string | null
  ingredients: string[]
  steps: string[]
  notes: string | null
  sourceLine?: string
}

function sanitizeFileName(title: string): string {
  const t = title
    .trim()
    .replace(/[<>:"/\\|?*]/g, '')
    .replace(/\s+/g, '-')
    .slice(0, 80)
  return t.length > 0 ? t : 'recipe'
}

/** Stylized lemon strip (original artwork, not a copy of any specific template image). */
function appendLemonDecoration(container: HTMLElement): void {
  const wrap = document.createElement('div')
  wrap.innerHTML = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 76" width="100%" height="76" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
  <defs>
    <linearGradient id="lem" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#f7e066"/>
      <stop offset="100%" style="stop-color:#e8c62e"/>
    </linearGradient>
  </defs>
  <ellipse cx="120" cy="42" rx="52" ry="24" fill="#2d5a32" transform="rotate(-28 120 42)"/>
  <ellipse cx="890" cy="38" rx="48" ry="22" fill="#2d5a32" transform="rotate(22 890 38)"/>
  <ellipse cx="210" cy="40" rx="30" ry="26" fill="url(#lem)" stroke="#d4a20a" stroke-width="0.8" transform="rotate(-8 210 40)"/>
  <circle cx="268" cy="44" r="22" fill="url(#lem)" stroke="#d4a20a" stroke-width="0.6"/>
  <ellipse cx="760" cy="38" rx="34" ry="28" fill="url(#lem)" stroke="#d4a20a" stroke-width="0.8" transform="rotate(12 760 38)"/>
  <ellipse cx="820" cy="46" rx="26" ry="22" fill="#f5d94a" stroke="#d4a20a" stroke-width="0.6" transform="rotate(-18 820 46)"/>
  <path d="M95 48 Q130 20 165 45" fill="none" stroke="#3d7c45" stroke-width="3" stroke-linecap="round"/>
  <path d="M900 50 Q865 25 830 48" fill="none" stroke="#3d7c45" stroke-width="3" stroke-linecap="round"/>
</svg>`
  wrap.style.cssText = 'width:100%;line-height:0;margin:0 0 8px;'
  container.appendChild(wrap)
}

function buildRecipeCardEl(input: RecipeCardExportInput): HTMLDivElement {
  const ink = '#1a2332'
  const wrap = document.createElement('div')
  wrap.className = 'recipe-pdf-root'
  wrap.style.cssText = [
    'box-sizing:border-box',
    'width:1000px',
    'min-height:400px',
    'padding:20px 28px 28px',
    'background:#ffffff',
    `color:${ink}`,
    "font-family:Georgia,'Times New Roman',Times,serif",
    '-webkit-font-smoothing:antialiased',
  ].join(';')

  appendLemonDecoration(wrap)

  const header = document.createElement('div')
  header.style.cssText =
    'display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:12px 24px;margin-bottom:6px;'
  const scriptTitle = document.createElement('div')
  scriptTitle.textContent = 'Recipe'
  scriptTitle.style.cssText = [
    "font-family:'Brush Script MT','Segoe Script','Lucida Handwriting',cursive",
    'font-size:3.1rem',
    'line-height:1',
    `color:${ink}`,
    'font-weight:400',
  ].join(';')

  const labelSerif = "Georgia,'Times New Roman',Times,serif"
  const handwriting =
    "'Segoe Script','Bradley Hand ITC','MV Boli','Lucida Handwriting','Apple Chancery','Brush Script MT',cursive"
  const hwBase = `font-family:${handwriting};font-weight:400;letter-spacing:0.02em;color:${ink};`

  function appendMetaSegment(container: HTMLElement, label: string, value: string): void {
    const seg = document.createElement('span')
    seg.style.cssText =
      'display:inline-flex;align-items:baseline;gap:0.45rem;padding:0 0.35rem;flex:0 0 auto;'
    const lab = document.createElement('span')
    lab.textContent = label
    lab.style.cssText = [
      `font-family:${labelSerif}`,
      'font-size:0.62rem',
      'font-weight:700',
      'letter-spacing:0.14em',
      'font-variant:small-caps',
      'text-transform:uppercase',
      `color:${ink}`,
      'white-space:nowrap',
    ].join(';')
    const val = document.createElement('span')
    val.textContent = value
    val.style.cssText = [
      `font-family:${handwriting}`,
      'font-size:1.02rem',
      'font-weight:400',
      'letter-spacing:0.03em',
      `color:${ink}`,
      'line-height:1.15',
    ].join(';')
    seg.appendChild(lab)
    seg.appendChild(val)
    container.appendChild(seg)
  }

  const meta = document.createElement('div')
  meta.style.cssText = [
    'display:flex',
    'flex-wrap:wrap',
    'justify-content:flex-end',
    'align-items:baseline',
    'column-gap:2.25rem',
    'row-gap:0.35rem',
    'padding-left:1rem',
  ].join(';')
  const prep = formatRecipeDuration(input.prepTime)
  const tot = formatRecipeDuration(input.totalTime)
  const n =
    Number.isFinite(input.servesCount) && input.servesCount > 0 ? input.servesCount : 4
  appendMetaSegment(meta, 'Serves', String(n))
  appendMetaSegment(meta, 'Prep', prep)
  appendMetaSegment(meta, 'Total', tot)

  header.appendChild(scriptTitle)
  header.appendChild(meta)
  wrap.appendChild(header)

  const sub = document.createElement('div')
  sub.textContent = input.recipeTitle || 'Untitled'
  sub.style.cssText = [
    hwBase,
    'font-size:1.35rem',
    'line-height:1.35',
    'margin:4px 0 14px',
    `border-bottom:1px solid ${ink}`,
    'padding-bottom:8px',
  ].join(';')
  wrap.appendChild(sub)

  if (input.description && input.description.trim()) {
    const d = document.createElement('p')
    d.textContent = input.description
    d.style.cssText = [
      hwBase,
      'font-size:0.98rem',
      'line-height:1.55',
      'color:#2a3544',
      'margin:0 0 14px',
      'font-style:italic',
    ].join(';')
    wrap.appendChild(d)
  }

  const grid = document.createElement('div')
  grid.style.cssText = 'display:grid;grid-template-columns:1fr 2fr;gap:0 28px;align-items:start;'

  const ingCol = document.createElement('div')
  const ingLab = document.createElement('div')
  ingLab.textContent = 'INGREDIENTS:'
  ingLab.style.cssText = [
    `font-family:${labelSerif}`,
    'font-size:0.72rem',
    'letter-spacing:0.12em',
    'font-weight:700',
    'margin-bottom:10px',
    'border-bottom:1px solid #2c3a4d',
    'padding-bottom:4px',
  ].join(';')
  ingCol.appendChild(ingLab)
  const ul = document.createElement('ul')
  ul.style.cssText = [
    'margin:0',
    'padding:0',
    'list-style:none',
    hwBase,
    'font-size:0.95rem',
    'line-height:1.5',
  ].join(';')
  if (input.ingredients.length === 0) {
    const li = document.createElement('li')
    li.textContent = '—'
    li.style.cssText = 'border-bottom:1px solid #c5cdd8;padding:6px 0;'
    ul.appendChild(li)
  } else {
    for (const line of input.ingredients) {
      const li = document.createElement('li')
      li.textContent = line
      li.style.cssText = 'border-bottom:1px solid #c5cdd8;padding:7px 0;min-height:1.2em;'
      ul.appendChild(li)
    }
  }
  ingCol.appendChild(ul)
  grid.appendChild(ingCol)

  const dirCol = document.createElement('div')
  const dirLab = document.createElement('div')
  dirLab.textContent = 'DIRECTIONS:'
  dirLab.style.cssText = [
    `font-family:${labelSerif}`,
    'font-size:0.72rem',
    'letter-spacing:0.12em',
    'font-weight:700',
    'margin-bottom:10px',
    'border-bottom:1px solid #2c3a4d',
    'padding-bottom:4px',
  ].join(';')
  dirCol.appendChild(dirLab)
  const ol = document.createElement('ol')
  ol.style.cssText = [
    'margin:0',
    'padding-left:1.15rem',
    hwBase,
    'font-size:0.95rem',
    'line-height:1.52',
  ].join(';')
  if (input.steps.length === 0) {
    const li = document.createElement('li')
    li.textContent = '—'
    li.style.cssText = 'border-bottom:1px solid #c5cdd8;padding:7px 0;margin-left:-0.2rem;'
    ol.appendChild(li)
  } else {
    for (const step of input.steps) {
      const li = document.createElement('li')
      li.textContent = step
      li.style.cssText = 'border-bottom:1px solid #c5cdd8;padding:7px 0;margin-bottom:2px;'
      ol.appendChild(li)
    }
  }
  dirCol.appendChild(ol)
  grid.appendChild(dirCol)
  wrap.appendChild(grid)

  const kitchen = document.createElement('div')
  kitchen.style.cssText = [
    `font-family:${labelSerif}`,
    'margin-top:18px',
    'padding-top:10px',
    `border-top:2px solid ${ink}`,
    'font-size:0.72rem',
    'letter-spacing:0.1em',
    'font-weight:700',
  ].join(';')
  kitchen.textContent = 'FROM THE KITCHEN OF:'
  wrap.appendChild(kitchen)
  if (input.sourceLine) {
    const src = document.createElement('div')
    src.textContent = input.sourceLine
    src.style.cssText = [
      hwBase,
      'font-size:0.96rem',
      'line-height:1.45',
      'margin-top:6px',
      'color:#2a3544',
    ].join(';')
    wrap.appendChild(src)
  }

  if (input.notes != null && input.notes.trim()) {
    const nHead = document.createElement('div')
    nHead.textContent = 'additional notes and instructions fetched during Extraction'
    nHead.style.cssText = [
      hwBase,
      'margin-top:14px',
      'font-size:0.88rem',
      'line-height:1.35',
      'font-weight:600',
      'color:#2a3544',
    ].join(';')
    wrap.appendChild(nHead)
    const nBody = document.createElement('div')
    nBody.textContent = input.notes
    nBody.style.cssText = [
      hwBase,
      'margin-top:8px',
      'font-size:0.94rem',
      'line-height:1.52',
      'color:#2a3544',
      'white-space:pre-wrap',
    ].join(';')
    wrap.appendChild(nBody)
  }

  return wrap
}

/** Fraction of row pixels that look like ink (text), ~0 for blank gaps between lines. */
function rowInkFraction(data: ImageData, width: number, dy: number): number {
  const buf = data.data
  const row = dy * width * 4
  let ink = 0
  let samples = 0
  for (let x = 0; x < width; x += 2) {
    const i = row + x * 4
    const r = buf[i] ?? 255
    const g = buf[i + 1] ?? 255
    const b = buf[i + 2] ?? 255
    const a = buf[i + 3] ?? 255
    samples++
    if (a < 200) continue
    const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    if (lum < 0.86) ink++
  }
  return samples > 0 ? ink / samples : 0
}

/**
 * Move the horizontal cut from the “ideal” page break down to a nearby row with
 * minimal ink (gap between lines / whitespace) so text isn’t sliced in half.
 */
function findSliceEndY(
  source: HTMLCanvasElement,
  srcY: number,
  idealEndY: number,
  searchBackPx: number,
): number {
  const fullH = source.height
  const fullW = source.width
  const targetY = Math.min(Math.ceil(idealEndY), fullH)
  const minY = Math.max(Math.floor(srcY) + 16, targetY - searchBackPx)
  if (targetY <= minY + 2) return targetY

  const ctx = source.getContext('2d', { willReadFrequently: true })
  if (!ctx) return targetY

  const bandTop = minY
  const bandH = targetY - bandTop
  const data = ctx.getImageData(0, bandTop, fullW, bandH)

  let bestDy = bandH - 1
  let bestScore = Infinity
  const topPad = 2
  for (let dy = bandH - 1; dy >= topPad; dy--) {
    const ink = rowInkFraction(data, fullW, dy)
    const bias = ((bandH - 1 - dy) / Math.max(bandH, 1)) * 0.015
    const score = ink + bias
    if (score < bestScore) {
      bestScore = score
      bestDy = dy
    }
  }

  let cutY = bandTop + bestDy + 1
  const minSlice = 32
  if (cutY <= srcY + minSlice) {
    cutY = Math.min(targetY, Math.max(srcY + minSlice, Math.floor(srcY + (targetY - srcY) * 0.45)))
  }
  return Math.min(Math.max(cutY, srcY + 1), fullH)
}

function addImagePages(pdf: jsPDF, canvas: HTMLCanvasElement, marginMm: number): void {
  const pageW = pdf.internal.pageSize.getWidth() - 2 * marginMm
  const pageH = pdf.internal.pageSize.getHeight() - 2 * marginMm
  const imgW_mm = pageW
  const fullImgH_mm = (canvas.height * imgW_mm) / canvas.width

  if (fullImgH_mm <= pageH + 0.01) {
    pdf.addImage(canvas.toDataURL('image/png', 1.0), 'PNG', marginMm, marginMm, imgW_mm, fullImgH_mm)
    return
  }

  let srcYpx = 0
  let first = true
  while (srcYpx < canvas.height - 0.5) {
    if (!first) pdf.addPage()
    first = false

    const remainingPx = canvas.height - srcYpx
    const maxSlicePx = (pageH * canvas.width) / imgW_mm
    const idealEnd = srcYpx + Math.min(remainingPx, maxSlicePx)
    const searchBack = Math.min(160, Math.max(48, maxSlicePx * 0.22))
    const sliceEndY = findSliceEndY(canvas, srcYpx, idealEnd, searchBack)
    let slicePx = sliceEndY - srcYpx
    if (slicePx < 1) slicePx = Math.min(remainingPx, maxSlicePx)
    if (slicePx < 24 && remainingPx > 24) {
      slicePx = Math.min(remainingPx, Math.max(40, maxSlicePx * 0.62))
    }
    const sliceH_mm = (slicePx * imgW_mm) / canvas.width

    const slice = document.createElement('canvas')
    slice.width = canvas.width
    slice.height = Math.ceil(slicePx)
    const ctx = slice.getContext('2d')
    if (!ctx) break
    ctx.drawImage(canvas, 0, srcYpx, canvas.width, slicePx, 0, 0, canvas.width, slicePx)
    pdf.addImage(slice.toDataURL('image/png', 1.0), 'PNG', marginMm, marginMm, imgW_mm, sliceH_mm)
    srcYpx += slicePx
  }
}

/**
 * Landscape recipe card PDF (html2canvas). Element is moved off-screen at full opacity — opacity 0 yields blank captures in many browsers.
 */
export async function exportRecipeCardPdf(input: RecipeCardExportInput): Promise<void> {
  const fileStem = sanitizeFileName(input.recipeTitle)
  const card = buildRecipeCardEl(input)
  card.setAttribute('data-recipe-pdf-export', '1')
  Object.assign(card.style, {
    position: 'fixed',
    left: '-12000px',
    top: '0',
    zIndex: '2147483646',
    opacity: '1',
    visibility: 'visible',
    pointerEvents: 'none',
  })
  document.body.appendChild(card)

  try {
    await (document.fonts?.ready ?? Promise.resolve())
    await new Promise<void>((r) => requestAnimationFrame(() => r()))
    await new Promise<void>((r) => requestAnimationFrame(() => r()))

    const w = Math.ceil(card.offsetWidth) || 1000
    const h = Math.ceil(card.scrollHeight) || 400

    const canvas = await html2canvas(card, {
      scale: 2,
      backgroundColor: '#ffffff',
      useCORS: true,
      logging: false,
      width: w,
      height: h,
      windowWidth: w,
      windowHeight: h,
      onclone: (clonedDoc) => {
        const el = clonedDoc.querySelector('[data-recipe-pdf-export="1"]') as HTMLElement | null
        if (el) {
          el.style.opacity = '1'
          el.style.visibility = 'visible'
        }
      },
    })

    if (canvas.width < 2 || canvas.height < 2) {
      throw new Error('PDF render produced an empty canvas')
    }

    const pdf = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'landscape' })
    addImagePages(pdf, canvas, 10)
    pdf.save(`${fileStem}.pdf`)
  } finally {
    card.remove()
  }
}
