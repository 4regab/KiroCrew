import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useKeepAlive } from '../app-sdk/keepAlive'
import InstalledAppSurface from '../components/InstalledAppSurface'
import { KeptAppPagesProvider } from '../components/KeptAppPages'
import { NavigationLeaveGuardProvider, useRegisterNavigationLeaveGuard } from '../components/NavigationLeaveGuard'
import { KeptAppPagesStore, MAX_KEPT_APP_PAGES } from '../components/keptAppPagesStore'
import { useLocalCrewHidden } from '../components/overlayRoot'

const { getApp, lifecycle } = vi.hoisted(() => ({
  getApp: vi.fn(),
  lifecycle: { mounts: [] as string[], unmounts: [] as string[] },
}))

vi.mock('../api/client', () => ({ api: { getApp } }))
vi.mock('../lib/appViewTelemetry', () => ({ useAppViewMetrics: () => {} }))
vi.mock('../i18n/t', () => ({
  i18nT: (key: string, params?: { name?: string; n?: string; error?: string }) => {
    if (params?.error !== undefined) return `${key}:${params.name}:${params.error}`
    const value = params?.name ?? params?.n
    return value ? `${key}:${value}` : key
  },
}))
vi.mock('../components/ui', () => ({
  Btn: ({ children, danger: _danger, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { danger?: boolean }) => (
    <button type="button" {...rest}>{children}</button>
  ),
}))
vi.mock('../components/ErrorNotice', () => ({
  default: ({ message, onDismiss, testId, impact, askAgent }: {
    message: string
    onDismiss?: () => void
    testId?: string
    impact?: string
    askAgent?: boolean
  }) => (
    <div data-testid={testId} data-impact={impact} data-ask-agent={String(askAgent ?? false)}>
      <span>{message}</span>
      {onDismiss && <button type="button" onClick={onDismiss}>dismiss-status-error</button>}
    </div>
  ),
}))
// The real AppHost loads a bundle over the network. This one renders the app the
// same way the real one does for keep-alive: the control it is handed becomes the
// KeepAliveContext the app's `useKeepAlive` reads.
vi.mock('../components/AppHost', async () => {
  const { KeepAliveContext } = await import('../app-sdk/keepAlive')
  return {
    default: ({ app, keepAlive, active }: { app: { name: string }; keepAlive?: never; active?: boolean }) => (
      <KeepAliveContext.Provider value={keepAlive ?? null}>
        <ProbeApp name={app.name} active={active !== false} />
      </KeepAliveContext.Provider>
    ),
  }
})

function ProbeApp({ name, active }: { name: string; active: boolean }) {
  const [busy, setBusy] = useState(false)
  const status = useKeepAlive(busy)
  const offScreen = useLocalCrewHidden()
  useEffect(() => {
    lifecycle.mounts.push(name)
    return () => { lifecycle.unmounts.push(name) }
  }, [name])
  return (
    <div data-testid={`probe-${name}`} data-status={status} data-active={String(active)} data-off-screen={String(offScreen)}>
      <button type="button" onClick={() => setBusy(b => !b)}>{`toggle-${name}`}</button>
    </div>
  )
}

const record = (name: string) => ({ name, displayName: name, enabled: true, manifest: { ui: { entry: 'index.mjs' } } })

function RoutedApp() {
  const { name = '' } = useParams()
  return <InstalledAppSurface app={record(name)} requestedName={name} />
}

function Nav() {
  const navigate = useNavigate()
  const location = useLocation()
  return (
    <>
      <span data-testid="location">{location.pathname + location.search + location.hash}</span>
      <button type="button" onClick={() => navigate('/chat')}>go-chat</button>
      {['a', 'b', 'c'].map(n => <button key={n} type="button" onClick={() => navigate(`/apps/${n}`)}>{`go-${n}`}</button>)}
    </>
  )
}

function NavigationVeto({ guard }: { guard: () => boolean }) {
  useRegisterNavigationLeaveGuard(guard)
  return null
}

function renderShell(start = '/apps/a', guard?: () => boolean) {
  return render(
    <MemoryRouter initialEntries={[start]}>
      <NavigationLeaveGuardProvider>
        {guard && <NavigationVeto guard={guard} />}
        <KeptAppPagesProvider>
          <Routes>
            <Route path="/apps/:name" element={<RoutedApp />} />
            <Route path="/chat" element={<div data-testid="chat-page" />} />
          </Routes>
          <Nav />
        </KeptAppPagesProvider>
      </NavigationLeaveGuardProvider>
    </MemoryRouter>,
  )
}

const click = (label: string) => fireEvent.click(screen.getByText(label))

beforeEach(() => {
  getApp.mockImplementation((name: string) => Promise.resolve(record(name)))
})

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
  lifecycle.mounts.length = 0
  lifecycle.unmounts.length = 0
})

describe('kept app pages', () => {
  it('closes a page that is not busy when the person leaves it, as before', () => {
    renderShell()
    expect(screen.getByTestId('probe-a').dataset.status).toBe('off')
    click('go-chat')
    expect(screen.queryByTestId('probe-a')).toBeNull()
    expect(lifecycle.unmounts).toEqual(['a'])
    expect(screen.queryByTestId('kept-app-indicator')).toBeNull()
  })

  it('keeps a busy page mounted and hidden, names it, and Open returns to the same instance', () => {
    renderShell()
    click('toggle-a')
    expect(screen.getByTestId('probe-a').dataset.status).toBe('on')
    click('go-chat')
    expect(screen.getByTestId('chat-page')).toBeInTheDocument()
    expect(lifecycle.unmounts).toEqual([])
    const probe = screen.getByTestId('probe-a')
    expect(probe.dataset.active).toBe('false')
    expect(probe.closest('[data-testid="kept-app-park"]')).not.toBeNull()
    expect(screen.getByTestId('kept-app-indicator').textContent).toContain('components.keptAppPages.running:a')

    click('components.keptAppPages.open')
    expect(screen.getByTestId('probe-a').closest('[data-testid="kept-app-slot"]')).not.toBeNull()
    expect(screen.getByTestId('probe-a').dataset.active).toBe('true')
    expect(lifecycle.mounts).toEqual(['a'])
    expect(lifecycle.unmounts).toEqual([])
    expect(screen.queryByTestId('kept-app-indicator')).toBeNull()
  })

  it('keeps the current page when its leave guard refuses Open', () => {
    const guard = vi.fn(() => false)
    renderShell('/apps/a', guard)
    click('toggle-a')
    click('go-chat')

    click('components.keptAppPages.open')

    expect(guard).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('location').textContent).toBe('/chat')
    expect(screen.getByTestId('chat-page')).toBeInTheDocument()
    expect(screen.getByTestId('probe-a').dataset.active).toBe('false')
    expect(screen.getByTestId('kept-app-indicator')).toBeInTheDocument()
  })

  it('unmounts the hidden page when the person presses Stop', () => {
    renderShell()
    click('toggle-a')
    click('go-chat')
    click('components.keptAppPages.stop_named:a')
    expect(screen.queryByTestId('probe-a')).toBeNull()
    expect(lifecycle.unmounts).toEqual(['a'])
    expect(screen.queryByTestId('kept-app-indicator')).toBeNull()
  })

  it('shows each hidden app as a row in the page flow with exactly Open and Stop', () => {
    renderShell()
    click('toggle-a')
    click('go-b')
    click('toggle-b')
    click('go-chat')
    const indicator = screen.getByTestId('kept-app-indicator')
    expect(indicator.className.split(/\s+/)).not.toContain('fixed')
    expect(indicator.className.split(/\s+/)).toContain('sticky')
    const rows = screen.getAllByTestId('kept-app-row')
    expect(rows).toHaveLength(2)
    for (const row of rows) {
      expect(Array.from(row.querySelectorAll('button')).map(b => b.textContent)).toEqual([
        'components.keptAppPages.open',
        expect.stringMatching(/^components\.keptAppPages\.stop_named:/),
      ])
    }
  })

  it('takes a hidden page off screen for its own overlays and key handlers, and back when it is shown', () => {
    renderShell()
    click('toggle-a')
    expect(screen.getByTestId('probe-a').dataset.offScreen).toBe('false')
    click('go-chat')
    expect(screen.getByTestId('probe-a').dataset.offScreen).toBe('true')
    click('components.keptAppPages.open')
    expect(screen.getByTestId('probe-a').dataset.offScreen).toBe('false')
  })

  it('releases a hidden page as soon as its app says it is no longer busy', () => {
    renderShell()
    click('toggle-a')
    click('go-chat')
    click('toggle-a')
    expect(screen.queryByTestId('probe-a')).toBeNull()
    expect(lifecycle.unmounts).toEqual(['a'])
  })

  it(`refuses a keep-alive past ${MAX_KEPT_APP_PAGES} apps`, () => {
    renderShell()
    click('toggle-a')
    click('go-b')
    click('toggle-b')
    click('go-c')
    click('toggle-c')
    expect(screen.getByTestId('probe-a').dataset.status).toBe('on')
    expect(screen.getByTestId('probe-b').dataset.status).toBe('on')
    expect(screen.getByTestId('probe-c').dataset.status).toBe('refused')
    click('go-chat')
    expect(screen.queryByTestId('probe-c')).toBeNull()
    expect(screen.getAllByText(/^components\.keptAppPages\.running:/)).toHaveLength(2)
  })

  it.each([
    ['turned off', () => getApp.mockResolvedValue({ ...record('a'), enabled: false })],
    ['uninstalled', () => getApp.mockRejectedValue(Object.assign(new Error('not found'), { status: 404 }))],
  ])('releases a hidden page whose app was %s', async (_label, answer) => {
    answer()
    renderShell()
    click('toggle-a')
    click('go-chat')
    act(() => { window.dispatchEvent(new Event('mc:apps-changed')) })
    await waitFor(() => expect(screen.queryByTestId('probe-a')).toBeNull())
    expect(getApp).toHaveBeenCalledWith('a')
  })

  it('re-reads a page the moment it is hidden, so an app turned off on screen is released', async () => {
    getApp.mockResolvedValue({ ...record('a'), enabled: false })
    renderShell()
    click('toggle-a')
    click('go-chat')
    await waitFor(() => expect(screen.queryByTestId('probe-a')).toBeNull())
    expect(getApp).toHaveBeenCalledWith('a')
  })

  it('Open after switching to another app returns to the first app, not the second', () => {
    renderShell()
    click('toggle-a')
    click('go-b')
    click('go-chat')
    click('components.keptAppPages.open')
    expect(screen.getByTestId('location').textContent).toBe('/apps/a')
    expect(lifecycle.mounts.filter(n => n === 'a')).toHaveLength(1)
  })

  it('Open returns to the URL the page opened at, query included', () => {
    getApp.mockResolvedValue(record('a'))
    renderShell('/apps/a?view=live#now')
    click('toggle-a')
    click('go-chat')
    click('components.keptAppPages.open')
    expect(screen.getByTestId('location').textContent).toBe('/apps/a?view=live#now')
  })

  it('keeps a hidden page whose app is still on', async () => {
    getApp.mockResolvedValue(record('a'))
    renderShell()
    click('toggle-a')
    click('go-chat')
    act(() => { window.dispatchEvent(new Event('mc:apps-changed')) })
    await waitFor(() => expect(getApp).toHaveBeenCalledWith('a'))
    await act(async () => { await Promise.resolve() })
    expect(screen.getByTestId('probe-a')).toBeInTheDocument()
  })

  it('shows a partial error without stopping the page when its status check fails, then clears it after a successful re-read', async () => {
    getApp.mockRejectedValue(Object.assign(new Error('service unavailable'), { status: 503 }))
    renderShell()
    click('toggle-a')
    click('go-chat')

    const notice = await screen.findByTestId('kept-app-status-error-a')
    expect(notice).toHaveTextContent('components.keptAppPages.status_check_failed:a:service unavailable')
    expect(notice.dataset.impact).toBe('partial')
    expect(notice.dataset.askAgent).toBe('false')
    expect(screen.getByTestId('probe-a')).toBeInTheDocument()

    getApp.mockResolvedValue(record('a'))
    act(() => { window.dispatchEvent(new Event('mc:apps-changed')) })
    await waitFor(() => expect(screen.queryByTestId('kept-app-status-error-a')).toBeNull())
    expect(screen.getByTestId('probe-a')).toBeInTheDocument()
  })

  it('dismisses a status-check error without stopping the hidden page', async () => {
    getApp.mockRejectedValue(Object.assign(new Error('network unavailable'), { status: 503 }))
    renderShell()
    click('toggle-a')
    click('go-chat')

    await screen.findByTestId('kept-app-status-error-a')
    click('dismiss-status-error')
    expect(screen.queryByTestId('kept-app-status-error-a')).toBeNull()
    expect(screen.getByTestId('probe-a')).toBeInTheDocument()
  })

  it('answers unsupported on a surface that cannot keep the page', () => {
    render(<ProbeApp name="panel" active />)
    click('toggle-panel')
    expect(screen.getByTestId('probe-panel').dataset.status).toBe('unsupported')
  })
})

describe('KeptAppPagesStore', () => {
  it('drops a hidden page when its app crashes and keeps a visible one on its crash notice', () => {
    const store = new KeptAppPagesStore()
    const slotA = document.createElement('div')
    const slotB = document.createElement('div')
    store.attach(record('a'), slotA, '/apps/a')
    store.attach(record('b'), slotB, '/apps/b')
    store.request('a', true)
    store.request('b', true)
    store.detach('a', slotA)
    store.crashed('a')
    store.crashed('b')
    expect(store.getSnapshot().map(p => [p.app.name, p.busy])).toEqual([['b', false]])
  })

  it('ignores a detach from a slot the page has already left', () => {
    const store = new KeptAppPagesStore()
    const first = document.createElement('div')
    const second = document.createElement('div')
    store.attach(record('a'), first, '/apps/a')
    store.attach(record('a'), second, '/apps/a')
    store.detach('a', first)
    expect(store.getSnapshot()[0]?.slot).toBe(second)
  })
})
