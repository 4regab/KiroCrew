import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { WaitGame, WAIT_GAME_DELAY_MS } from './WaitGame'
import laundryCss from './laundry.css?raw'
import { initI18n } from '../../i18n'

const engine = vi.hoisted(() => ({ start: vi.fn(), stop: vi.fn() }))
const host = vi.hoisted(() => ({ local: true }))
vi.mock('../../lib/loopbackHost', () => ({ isLoopbackHost: () => host.local }))
vi.mock('./engine', () => ({
  startLaundry: (...args: unknown[]) => { engine.start(...args); return { restart: () => {}, stop: engine.stop } },
}))

beforeEach(() => {
  initI18n('en')
  engine.start.mockClear()
  engine.stop.mockClear()
  host.local = true
  vi.useFakeTimers()
})
afterEach(() => { vi.useRealTimers() })

const space = () => act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true })) })

describe('WaitGame', () => {
  it('renders nothing and starts no game until the wait has lasted 30 s', () => {
    render(<WaitGame />)
    act(() => { vi.advanceTimersByTime(WAIT_GAME_DELAY_MS - 1) })
    expect(screen.queryByTestId('wait-game')).toBeNull()
    expect(engine.start).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(1) })
    expect(screen.getByTestId('wait-game')).toBeTruthy()
    expect(engine.start).toHaveBeenCalledTimes(1)
  })

  it('starts at once on Space when no control has focus', () => {
    render(<WaitGame />)
    space()
    expect(screen.getByTestId('wait-game')).toBeTruthy()
    expect(engine.start).toHaveBeenCalledTimes(1)
  })

  it('leaves Space to a focused control', () => {
    render(<><button type="button">Retry</button><WaitGame /></>)
    screen.getByRole('button', { name: 'Retry' }).focus()
    space()
    expect(screen.queryByTestId('wait-game')).toBeNull()
  })

  it('leaves Space to a control inside a shadow root, which the document reports only as its host', () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const inner = host.attachShadow({ mode: 'open' })
    const button = document.createElement('button')
    inner.appendChild(button)
    render(<WaitGame />)
    act(() => { button.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true, composed: true, cancelable: true })) })
    expect(screen.queryByTestId('wait-game')).toBeNull()
    host.remove()
  })

  it('offers nothing on a dashboard reached through another name, such as a tunnel', () => {
    host.local = false
    render(<WaitGame />)
    space()
    act(() => { vi.advanceTimersByTime(WAIT_GAME_DELAY_MS) })
    expect(screen.queryByTestId('wait-game')).toBeNull()
    expect(engine.start).not.toHaveBeenCalled()
  })

  it('stops the game when the wait surface goes away', () => {
    const { unmount } = render(<WaitGame />)
    space()
    unmount()
    expect(engine.stop).toHaveBeenCalledTimes(1)
  })

  it('labels the game and its controls from the catalog', () => {
    render(<WaitGame surface="card" />)
    space()
    const game = screen.getByRole('group', { name: 'Laundry folding game' })
    expect(game.getAttribute('style')).toContain('var(--card)')
    // A visible label that names the game: beside "Updating and restarting…", "Start over" reads as restarting the update.
    expect(screen.getByRole('button', { name: 'Reset game' }).textContent).toContain('Reset game')
    expect(screen.getByText('Fold something while you wait.')).toBeTruthy()
  })

  it('shows pointer users how to play from the start', () => {
    render(<WaitGame />)
    space()
    expect(screen.getByText('Drag clothes to the table, tap to fold, drag to the pile.')).toBeTruthy()
  })

  it('offers the card game nowhere in a window under 480 px tall, where the dialog would cut it off', () => {
    const query = (matches: boolean) => vi.stubGlobal('matchMedia', (q: string) => ({ matches: matches && q.includes('max-height'), media: q, addEventListener: () => {}, removeEventListener: () => {} }))
    query(true)
    const { unmount } = render(<WaitGame surface="card" />)
    space()
    expect(screen.queryByTestId('wait-game')).toBeNull()
    unmount()
    render(<WaitGame />)
    space()
    expect(screen.getByTestId('wait-game')).toBeTruthy()
    vi.unstubAllGlobals()
  })
})

describe('WaitGame Space handling', () => {
  const press = (target: Element) => {
    const ev = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true })
    act(() => { target.dispatchEvent(ev) })
    return ev
  }
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('leaves Space alone, unprevented, where the card game is never offered', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('max-height'), media: q, addEventListener: () => {}, removeEventListener: () => {} }))
    render(<WaitGame surface="card" />)
    expect(press(document.body).defaultPrevented).toBe(false)
    expect(screen.queryByTestId('wait-game')).toBeNull()
  })

  it('leaves Space to a focused region that scrolls, and starts the game from a focused container that does not', () => {
    const region = document.createElement('div')
    region.tabIndex = -1
    region.style.overflowY = 'auto'
    document.body.appendChild(region)
    let hidden = 120
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(200)
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(() => 200 + hidden)
    const { unmount } = render(<WaitGame />)
    expect(press(region).defaultPrevented).toBe(false)
    expect(screen.queryByTestId('wait-game')).toBeNull()
    hidden = 0 // the same element with nothing to scroll is just a focused container, like a dialog's root
    expect(press(region).defaultPrevented).toBe(true)
    expect(screen.getByTestId('wait-game')).toBeTruthy()
    unmount()
    region.remove()
  })
})

describe('laundry chrome', () => {
  it('lets the counter and button wrap, with the button growing with its label rather than a fixed height', () => {
    render(<WaitGame />)
    space()
    const btn = screen.getByRole('button', { name: 'Reset game' })
    expect(btn.className).toContain('min-h-9')
    expect(btn.className).not.toMatch(/(^|\s)h-9(\s|$)/)
    expect(laundryCss).toMatch(/\.laundry-hud\{[^}]*flex-wrap:wrap/)
  })

  it('puts the keyboard hint after the counter row in the same column, never beside it', () => {
    render(<WaitGame />)
    space()
    const hud = screen.getByTestId('wait-game').querySelector('.laundry-hud')!
    const hint = screen.getByTestId('wait-game').querySelector('.laundry-hint')!
    expect(hud.parentElement).toBe(hint.parentElement)
    expect(hud.compareDocumentPosition(hint) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('reserves no width in the game chrome from a fixed pixel count', () => {
    expect(laundryCss).not.toMatch(/calc\(\s*100%\s*-\s*\d+px\s*\)/)
  })
})

describe('laundry catalog', () => {
  const catalogs = import.meta.glob('../../i18n/locales/*.json', { eager: true, import: 'default' }) as Record<string, { app?: Record<string, string> }>
  it('names the game on the reset control, and shows how to play, in every locale', () => {
    const files = Object.keys(catalogs)
    expect(files.length).toBeGreaterThanOrEqual(13)
    for (const f of files) {
      const app = catalogs[f].app ?? {}
      if (f.endsWith('/en.json')) continue
      expect(app.laundry_reset_game, f).toBeTruthy()
      expect(app.laundry_pointer_hint, f).toBeTruthy()
      expect(app.laundry_start_over, f).toBeUndefined()
      // The help text names the reset control once: a leftover of the old wording beside the new one reads as a garbled phrase.
      // (en-XA is a generated pseudo-locale: its help text is transliterated, so the label is not found verbatim there.)
      if (!f.endsWith('/en-XA.json')) expect(app.laundry_help.split(app.laundry_reset_game).length - 1, f).toBe(1)
    }
  })
})
