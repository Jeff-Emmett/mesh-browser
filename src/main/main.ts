import { app, BrowserWindow } from 'electron'

import { pythonManager } from './processes'
import { registerProtocolSchemes, setupProtocolHandlers } from '@protocol-handlers/protocol-schemes'
import { createMainWindow, loadInBrowser, focusMainWindow } from './windows'

registerProtocolSchemes()

/* Single-instance lock — the desktop launcher / xdg-mime handler
 * spawns a fresh `mesh-browser <url>` process whenever an rweb://
 * link is clicked. Without this lock, every click opens a new window
 * AND starts a duplicate Python backend (which fights for the same
 * Reticulum identity). Acquiring the lock means subsequent launches
 * exit early; the existing process picks up the URL via the
 * second-instance event below.
 */
const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
}

app.on('second-instance', (_event, argv) => {
  /* User clicked an rweb:// link (or otherwise re-invoked the app)
   * while it was already running. Pull the URL out of the new argv
   * and route it through the existing main window. */
  const url = pickRwebUrl(argv)
  if (url) loadInBrowser(url)
  focusMainWindow()
})

app.whenReady().then(handleWhenReady)
app.on('window-all-closed', handleWindowAllClosed)
app.on('before-quit', handleBeforeQuit)

/** Walk argv for an rweb:// URL. Accepts both `rweb://...` and
 *  `--url=rweb://...` shapes so xdg-mime / desktop-launcher quirks
 *  don't matter. Returns `null` when no URL is present. */
function pickRwebUrl(argv: readonly string[]): string | null {
  for (const arg of argv) {
    if (arg.startsWith('rweb://')) return arg
    if (arg.startsWith('--url=')) {
      const v = arg.slice('--url='.length)
      if (v.startsWith('rweb://')) return v
    }
  }
  return null
}

async function handleWhenReady() {
  await startReticulumBackend()
  setupProtocolHandlers()
  await createMainWindow()

  /* If the user launched `mesh-browser rweb://...`, navigate to it
   * after the window is ready. */
  const initialUrl = pickRwebUrl(process.argv)
  if (initialUrl) loadInBrowser(initialUrl)

  app.on('activate', handleActivate)
}

function handleActivate() {
  if (BrowserWindow.getAllWindows().length === 0) createMainWindow()
}

function handleWindowAllClosed() {
  if (process.platform !== 'darwin') app.quit()
}

async function handleBeforeQuit() {
  await stopReticulumBackend()
}

async function startReticulumBackend() {
  try {
    await pythonManager.start()
    console.log('Reticulum backend started successfully')
  } catch (error) {
    console.error('Failed to start Reticulum backend:', error)
  }
}

async function stopReticulumBackend() {
  try {
    await pythonManager.stop()
    console.log('Reticulum backend stopped successfully')
  } catch (error) {
    console.error('Failed to stop Reticulum backend:', error)
  }
}
