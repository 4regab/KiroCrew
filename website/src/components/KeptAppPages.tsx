/**
 * Kept app pages: the shell side of the app SDK's `useKeepAlive`.
 *
 * `KeptAppPagesProvider` wraps the shell's routes. With it in place every routed
 * installed-app page is rendered here, by `KeptAppPagesLayer`, and the route only
 * lends it an element to sit in (`KeptAppSlot`). A page whose app is busy when the
 * person leaves stays mounted, hidden, and `KeptAppPagesIndicator` names it on
 * every page with a way back to it and a way to stop it. Surfaces without the
 * provider (a popped-out window, a test that mounts a page alone) render the app
 * inline, as before. The store's own rules are in `keptAppPagesStore.ts`.
 */
import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { isNotFoundError } from '../api/apiError'
import { i18nT } from '../i18n/t'
import { errMessage } from '../utils/thunkError'
import AppHost from './AppHost'
import ErrorNotice from './ErrorNotice'
import { OffScreenOverlayScope } from './overlayRoot'
import { appDisplayName } from './appstore/appManifest'
import { KeptAppPagesStore, type KeptAppPage, type KeptAppRecord } from './keptAppPagesStore'
import { useGuardedLeave } from './NavigationLeaveGuard'
import { Btn } from './ui'

const KeptAppPagesContext = createContext<KeptAppPagesStore | null>(null)

/** The shell's store, or null on a surface that renders app pages inline. */
export function useKeptAppPages(): KeptAppPagesStore | null {
  return useContext(KeptAppPagesContext)
}

export function KeptAppPagesProvider({ children }: { children: ReactNode }) {
  const [store] = useState(() => new KeptAppPagesStore())
  return (
    <KeptAppPagesContext.Provider value={store}>
      {children}
      <KeptAppPagesLayer store={store} />
    </KeptAppPagesContext.Provider>
  )
}

/** The routed page's place on screen: the app's host element moves in and out of it. */
export function KeptAppSlot({ store, app }: { store: KeptAppPagesStore; app: KeptAppRecord }) {
  const slotRef = useRef<HTMLDivElement>(null)
  const { pathname, search, hash } = useLocation()
  const path = pathname + search + hash
  const latest = useRef({ app, path })
  latest.current = { app, path }
  const name = app.name
  useLayoutEffect(() => {
    const slot = slotRef.current
    if (!slot) return
    // shortcut: Open returns to the URL the page opened at; a page that moves its
    // own sub-path comes back to that first URL. Reading the URL at detach is wrong
    // on an app-to-app switch, whose last render already carries the next app's URL.
    store.attach(latest.current.app, slot, latest.current.path)
    return () => store.detach(name, slot)
  }, [store, name])
  useEffect(() => { store.update(app) }, [store, app])
  return <div ref={slotRef} className="contents" data-testid="kept-app-slot" />
}

function KeptAppPagesLayer({ store }: { store: KeptAppPagesStore }) {
  const pages = useSyncExternalStore(store.subscribe, store.getSnapshot)
  const parkRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    store.park = parkRef.current
    return () => { store.park = null }
  }, [store])
  const hidden = useMemo(() => pages.filter(page => page.slot === null), [pages])
  const { statusErrors, dismissStatusError } = useReleaseRemovedApps(store, hidden)
  return (
    <>
      <div ref={parkRef} hidden data-testid="kept-app-park" />
      {pages.map(page => createPortal(
        // A hidden page's dialogs, menus and key handlers go off screen with it.
        <OffScreenOverlayScope offScreen={page.slot === null}>
          <AppHost
            app={page.app}
            sessionKey="dashboard:ui"
            active={page.slot !== null}
            keepAlive={store.controlFor(page.app.name)}
            onCrash={() => store.crashed(page.app.name)}
          />
        </OffScreenOverlayScope>,
        page.host,
        page.app.name,
      ))}
      {/* Unmounted when nothing is hidden, so the next hidden app shows the full bar again. */}
      {hidden.length > 0 && (
        <KeptAppPagesIndicator
          store={store}
          hidden={hidden}
          statusErrors={statusErrors}
          dismissStatusError={dismissStatusError}
        />
      )}
    </>
  )
}

/** A hidden page whose app was turned off or uninstalled is released. The shell
 *  announces both with `mc:apps-changed`; only the newest re-read per app counts,
 *  so a slow reply from before the app came back on cannot stop it. */
function useReleaseRemovedApps(store: KeptAppPagesStore, hidden: readonly KeptAppPage[]) {
  const [statusErrors, setStatusErrors] = useState<ReadonlyMap<string, string>>(() => new Map())
  const statusErrorsRef = useRef(statusErrors)
  statusErrorsRef.current = statusErrors
  const replaceStatusErrors = (next: ReadonlyMap<string, string>) => {
    statusErrorsRef.current = next
    setStatusErrors(next)
  }
  const names = hidden.map(page => page.app.name).join('\n')
  useEffect(() => {
    const activeNames = new Set(names ? names.split('\n') : [])
    if ([...statusErrorsRef.current.keys()].some(name => !activeNames.has(name))) {
      replaceStatusErrors(new Map([...statusErrorsRef.current].filter(([name]) => activeNames.has(name))))
    }
    if (!names) return
    const reads = new Map<string, number>()
    const clearStatusError = (name: string) => {
      if (!statusErrorsRef.current.has(name)) return
      const next = new Map(statusErrorsRef.current)
      next.delete(name)
      replaceStatusErrors(next)
    }
    const recheck = () => {
      for (const name of names.split('\n')) {
        const read = (reads.get(name) ?? 0) + 1
        reads.set(name, read)
        api.getApp(name)
          .then((app: { enabled?: boolean } | null) => {
            if (reads.get(name) !== read) return
            clearStatusError(name)
            if (app?.enabled === false) store.releaseHidden(name)
          })
          .catch((e: unknown) => {
            if (reads.get(name) !== read) return
            if (isNotFoundError(e)) {
              clearStatusError(name)
              store.releaseHidden(name)
              return
            }
            replaceStatusErrors(new Map(statusErrorsRef.current).set(name, errMessage(e)))
          })
      }
    }
    window.addEventListener('mc:apps-changed', recheck)
    // A page hidden because its app just went off or away missed that event.
    recheck()
    return () => {
      window.removeEventListener('mc:apps-changed', recheck)
      reads.clear()
    }
  }, [store, names])
  const dismissStatusError = (name: string) => {
    if (!statusErrorsRef.current.has(name)) return
    const next = new Map(statusErrorsRef.current)
    next.delete(name)
    replaceStatusErrors(next)
  }
  return { statusErrors, dismissStatusError }
}

/** A strip at the end of the page, in flow, so it takes its own space and
 *  covers nothing; only Stop ends a hidden app. */
function KeptAppPagesIndicator({
  store,
  hidden,
  statusErrors,
  dismissStatusError,
}: {
  store: KeptAppPagesStore
  hidden: readonly KeptAppPage[]
  statusErrors: ReadonlyMap<string, string>
  dismissStatusError: (name: string) => void
}) {
  const navigate = useNavigate()
  const leave = useGuardedLeave()
  return (
    <div
      role="region"
      aria-label={i18nT('components.keptAppPages.region_label')}
      data-testid="kept-app-indicator"
      className="sticky bottom-0 z-40 shrink-0 flex flex-col gap-2 border-t border-border bg-bg-elevated px-3 py-2"
    >
      {hidden.map(page => {
        const name = appDisplayName(page.app)
        const statusError = statusErrors.get(page.app.name)
        return (
          <div key={page.app.name} data-testid="kept-app-row" className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-text">
            <span className="flex min-w-0 items-center gap-2">
              <span className="w-2 h-2 shrink-0 rounded-full bg-danger" aria-hidden="true" />
              <span className="min-w-0 truncate" role="status">{i18nT('components.keptAppPages.running', { name })}</span>
            </span>
            <span className="flex items-center gap-2">
              <Btn onClick={() => leave(() => navigate(page.path), page.path)}>{i18nT('components.keptAppPages.open')}</Btn>
              <Btn danger className="whitespace-nowrap" onClick={() => store.releaseHidden(page.app.name)}>{i18nT('components.keptAppPages.stop_named', { name })}</Btn>
            </span>
            {statusError !== undefined && (
              // No agent hand-off: it navigates away and may discard unsaved work in the hidden app.
              <ErrorNotice
                impact="partial"
                message={i18nT('components.keptAppPages.status_check_failed', { name, error: statusError })}
                onDismiss={() => dismissStatusError(page.app.name)}
                testId={`kept-app-status-error-${page.app.name}`}
                className="basis-full"
              />
            )}
          </div>
        )
      })}
    </div>
  )
}
