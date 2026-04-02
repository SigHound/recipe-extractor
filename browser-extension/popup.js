/** Base URL of the Recipe Extractor frontend (Vite default). Change if you use another port or deploy. */
const APP_ORIGIN = 'http://localhost:5173'

document.getElementById('extract')?.addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  const url = tab?.url
  if (!url) return

  try {
    const parsed = new URL(url)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      alert('Open a normal http(s) page, then try again.')
      return
    }
  } catch {
    alert('Could not read this tab’s URL.')
    return
  }

  const target = `${APP_ORIGIN}/?extract=${encodeURIComponent(url)}`
  await chrome.tabs.create({ url: target })
  window.close()
})
