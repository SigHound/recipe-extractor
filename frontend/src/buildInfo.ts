/** Injected at compile time in vite.config.ts */
export function appVersion(): string {
  return import.meta.env.VITE_APP_VERSION
}

export function buildTimeIso(): string {
  return import.meta.env.VITE_BUILD_TIME
}

export function buildTimeLabel(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
  } catch {
    return iso
  }
}
