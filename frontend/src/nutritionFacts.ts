/**
 * FDA Reference Daily Values (general nutrition labeling, 2,000 Cal diet).
 * @see https://www.fda.gov/food/nutrition-education-resources-materials/nutrition-facts-label
 */

export const FDA_DV_BY_ID: Record<string, number> = {
  FAT: 78,
  CHOCDF: 275,
  PROCNT: 50,
  FIBTG: 28,
  SUGAR: 50,
  NA: 2300,
  CHOLE: 300,
  VITA_RAE: 900,
  VITC: 90,
  CA: 1300,
  FE: 18,
  K: 4700,
}

/** Display label tweaks (FDA-style wording). */
export const FDA_LABEL_BY_ID: Record<string, string> = {
  FAT: 'Total Fat',
  CHOCDF: 'Total Carbohydrate',
  PROCNT: 'Protein',
  FIBTG: 'Dietary Fiber',
  SUGAR: 'Total Sugars',
  NA: 'Sodium',
  CHOLE: 'Cholesterol',
  VITA_RAE: 'Vitamin A',
  VITC: 'Vitamin C',
  CA: 'Calcium',
  FE: 'Iron',
  K: 'Potassium',
}

/** Subordinate rows (indented on the label). */
export const SUB_NUTRIENT_IDS = new Set(['FIBTG', 'SUGAR'])

/** Same order as backend `NUTRIENT_DISPLAY_ORDER` (kcal excluded). For skeleton rows. */
export const NUTRIENT_ROW_ORDER: string[] = [
  'PROCNT',
  'CHOCDF',
  'FAT',
  'FIBTG',
  'SUGAR',
  'NA',
  'CHOLE',
  'VITA_RAE',
  'VITC',
  'CA',
  'FE',
  'K',
]

/** Display units for skeleton rows (aligned with backend). */
export const NUTRIENT_UNIT_BY_ID: Record<string, string> = {
  PROCNT: 'g',
  CHOCDF: 'g',
  FAT: 'g',
  FIBTG: 'g',
  SUGAR: 'g',
  NA: 'mg',
  CHOLE: 'mg',
  VITA_RAE: 'µg',
  VITC: 'mg',
  CA: 'mg',
  FE: 'mg',
  K: 'mg',
}

export function percentDailyValue(nutrientId: string, amount: number): number | null {
  const dv = FDA_DV_BY_ID[nutrientId]
  if (dv == null || dv <= 0 || !Number.isFinite(amount)) return null
  return (amount / dv) * 100
}

export function formatPercentDv(pct: number | null): string {
  if (pct == null) return '—'
  if (pct <= 0) return '0%'
  if (pct < 1) return '<1%'
  if (pct >= 999) return '>999%'
  return `${Math.round(pct)}%`
}

export function caloriesPercentDailyValue(calories: number): number | null {
  if (!Number.isFinite(calories)) return null
  return (calories / 2000) * 100
}
