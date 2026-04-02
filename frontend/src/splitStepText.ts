/** Break long step text into shorter blocks for readability. */

export function splitStepIntoChunks(text: string): string[] {
  const t = text.trim()
  if (!t) return []

  const lines = t
    .split(/\n+/)
    .map((s) => s.trim())
    .filter(Boolean)
  if (lines.length > 1) return lines

  const single = lines[0] ?? t
  const sentences = single.split(/(?<=[.!?])\s+(?=[A-Z(])/).filter(Boolean)
  if (sentences.length > 1) {
    return sentences.map((s) => s.trim()).filter(Boolean)
  }

  return [single]
}
