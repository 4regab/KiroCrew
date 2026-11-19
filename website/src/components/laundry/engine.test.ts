import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { startLaundry, runTweenList, type LaundryText } from './engine'
import { GHOST_MARK, foldLayers, area, type Layer } from './geometry'
import engineSource from './engine.ts?raw'
import geometrySource from './geometry.ts?raw'
import waitGameSource from './WaitGame.tsx?raw'

// A frame queue the test drives by hand, and a clock it moves, so nothing waits on real time.
let queue: FrameRequestCallback[] = []
let clock = 1000
function frames(n = 40, step = 20) {
  for (let i = 0; i < n && queue.length; i++) {
    clock += step
    const run = queue
    queue = []
    for (const cb of run) cb(clock)
  }
}
function stubMotion(reduce: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: reduce && q.includes('reduced-motion'), media: q, addEventListener: () => {}, removeEventListener: () => {} }))
}

const TEXT: LaundryText = {
  folded: n => `F${n}`, item_basket: 'BASKET', item_table: 'TABLE', item_part_folded: 'PART', item_folded: 'FOLDED',
  placed_table: 'p-table', placed_basket: 'p-basket', placed_pile: 'p-pile', fold_done: 'done', fold_step: 'step',
  fold_first: 'first', table_full: 'full', picked_up: 'up', put_back: 'back', over_basket: 'o-b', over_table: 'o-t',
  over_pile: 'o-p', ghost: 'GHOST', pile_full: 'pile-full', started_over: 'again',
}

function mount() {
  const root = document.createElement('div')
  const part = (tag: string, cls?: string) => { const el = document.createElement(tag); if (cls) el.className = cls; return el }
  root.append(part('canvas'), part('div', 'laundry-keys'), ...['count', 'hud', 'hint', 'toast', 'caption', 'live'].map(c => part('p', `laundry-${c}`)))
  document.body.appendChild(root)
  return root
}

/** Seeded random (mulberry32), so each run plays the same game. */
function seeded(seed: number) {
  let a = seed >>> 0
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296 }
}

function press(b: Element, key: string) { b.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })); frames(5) }
function button(root: HTMLElement, ...labels: string[]) {
  return [...root.querySelectorAll('.laundry-keys button')].find(b => labels.includes(b.getAttribute('aria-label') || ''))
}
/** Under reduced motion every move lands at once: basket -> table, fold, pick up, over the pile, drop. */
function stackOne(root: HTMLElement) {
  press(button(root, 'BASKET')!, 'Enter')
  for (let i = 0; i < 4; i++) { const t = button(root, 'TABLE', 'PART'); if (!t) break; press(t, 'Enter') }
  const f = button(root, 'FOLDED')!
  press(f, ' ')
  press(f, ' ')
  frames(20, 50)
}

beforeEach(() => {
  queue = []
  clock = 1000
  vi.spyOn(performance, 'now').mockImplementation(() => clock)
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { queue.push(cb); return queue.length })
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.replaceChildren()
})

describe('laundry engine', () => {
  it('requests no frames once an idle scene has drawn under reduced motion', () => {
    stubMotion(true)
    const h = startLaundry(mount(), { text: () => TEXT, random: seeded(1) })
    frames()
    expect(queue).toHaveLength(0)
    h.stop()
  })

  it('keeps a frame loop for the idle bob when motion is allowed', () => {
    stubMotion(false)
    const h = startLaundry(mount(), { text: () => TEXT, random: seeded(1) })
    frames()
    expect(queue).toHaveLength(1)
    h.stop()
  })

  it('stop() cancels the frame and removes every window listener it added', () => {
    stubMotion(false)
    const add = vi.spyOn(window, 'addEventListener')
    const remove = vi.spyOn(window, 'removeEventListener')
    const root = mount()
    const h = startLaundry(root, { text: () => TEXT, random: seeded(2) })
    frames(3)
    h.stop()
    const added = add.mock.calls.map(c => c[1])
    expect(added.length).toBeGreaterThan(0)
    for (const fn of added) expect(remove.mock.calls.some(c => c[1] === fn)).toBe(true)
    expect(window.cancelAnimationFrame).toHaveBeenCalled()
    expect(root.querySelectorAll('.laundry-keys button')).toHaveLength(0)
  })

  it('folds, stacks and counts by keyboard, and the first ghost comes on the first or second stack', () => {
    stubMotion(true)
    for (const seed of [3, 4, 5, 6, 7, 8]) {
      let ghosts = 0
      const root = mount()
      const text = () => ({ ...TEXT, get ghost() { ghosts++; return 'GHOST' } })
      const h = startLaundry(root, { text, random: seeded(seed) })
      frames()
      stackOne(root)
      expect(root.querySelector('.laundry-count')?.textContent).toBe('F1')
      // The count is its own visible text, not a drawn stack beside a bare number.
      expect(root.querySelector('.laundry-count')?.children).toHaveLength(0)
      stackOne(root)
      expect(root.querySelector('.laundry-count')?.textContent).toBe('F2')
      expect(ghosts).toBeGreaterThanOrEqual(1)
      h.stop()
      root.remove()
    }
  })

  it('R on the game starts over, even when the host stops keys from bubbling past it', () => {
    stubMotion(true)
    const host = document.createElement('div')
    host.addEventListener('keydown', e => e.stopPropagation())
    const root = mount()
    host.append(root)
    document.body.append(host)
    let again = 0
    const h = startLaundry(root, { text: () => ({ ...TEXT, get started_over() { again++; return 'again' } }), random: seeded(11) })
    frames()
    stackOne(root)
    press(button(root, 'BASKET')!, 'r')
    expect(again).toBe(1)
    expect(root.querySelector('.laundry-count')?.textContent).toBe('F0')
    h.stop()
  })

  it('a small embed still picks an item up from a tap just outside its drawing', () => {
    stubMotion(true)
    const root = mount()
    // A card about 224 x 200 px, as on a 320 px phone: the scene draws at about a quarter scale.
    root.getBoundingClientRect = () => ({ x: 0, y: 0, left: 0, top: 0, width: 224, height: 200, right: 224, bottom: 200, toJSON: () => ({}) })
    const canvas = root.querySelector('canvas')!
    canvas.getBoundingClientRect = root.getBoundingClientRect
    const h = startLaundry(root, { text: () => TEXT, random: seeded(12) })
    frames()
    const btn = button(root, 'BASKET') as HTMLElement
    const left = parseFloat(btn.style.transform.match(/translate\(([-\d.]+)px/)![1]), top = parseFloat(btn.style.transform.match(/,([-\d.]+)px\)/)![1])
    const w = parseFloat(btn.style.width), hh = parseFloat(btn.style.height)
    // 6 px outside the item's box on the left, level with its middle: still picks it up.
    const at = { clientX: left - 6, clientY: top + hh / 2, pointerId: 1, pointerType: 'touch', button: 0, bubbles: true, cancelable: true }
    canvas.dispatchEvent(new PointerEvent('pointerdown', at))
    canvas.dispatchEvent(new PointerEvent('pointerup', at))
    frames()
    expect(button(root, 'TABLE')).toBeTruthy()
    expect(w).toBeGreaterThan(0)
    h.stop()
  })

  it('a screen reader\'s plain click folds an item and then puts it on the pile', () => {
    stubMotion(true)
    const root = mount()
    const h = startLaundry(root, { text: () => TEXT, random: seeded(13) })
    frames()
    const click = (b: Element) => { b.dispatchEvent(new MouseEvent('click', { detail: 0, bubbles: true })); frames(5) }
    clock += 1000 // no key pressed in the last 300 ms
    click(button(root, 'BASKET')!)
    for (let i = 0; i < 4; i++) { const t = button(root, 'TABLE', 'PART'); if (!t) break; click(t) }
    click(button(root, 'FOLDED')!)
    expect(root.querySelector('.laundry-count')?.textContent).toBe('F1')
    h.stop()
  })

  it('makes no request and touches no storage while it plays', () => {
    stubMotion(true)
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    const open = vi.spyOn(XMLHttpRequest.prototype, 'open')
    const ws = vi.fn()
    vi.stubGlobal('WebSocket', ws)
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const root = mount()
    const h = startLaundry(root, { text: () => TEXT, random: seeded(9) })
    frames()
    stackOne(root)
    h.restart()
    frames()
    h.stop()
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(open).not.toHaveBeenCalled()
    expect(ws).not.toHaveBeenCalled()
    expect(setItem).not.toHaveBeenCalled()
    // And none of the game's modules names a network or storage API, or a URL other than the SVG namespace.
    for (const src of [engineSource, geometrySource, waitGameSource]) {
      expect(src).not.toMatch(/\bfetch\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|localStorage|sessionStorage|indexedDB|import\(/)
      expect(src.replace(/http:\/\/www\.w3\.org\/2000\/svg/g, '')).not.toMatch(/https?:\/\//)
    }
  })
})

describe('laundry ghost retirement under reduced motion', () => {
  /** A canvas context that counts the ellipses each draw makes: the basket's handles are always two, a ghost's eyes add more. */
  function recordEllipses() {
    const perDraw: number[] = []
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((() => new Proxy({}, {
      get: (_t, p) => (p === 'clearRect' ? () => { perDraw.push(0) } : p === 'ellipse' ? () => { perDraw[perDraw.length - 1]++ } : () => {}),
      set: () => true,
    })) as never)
    return perDraw
  }
  /** Like `stackOne`, but each move gets the frames a real animation needs. */
  function playOne(root: HTMLElement) {
    const go = (b: Element | undefined, key: string) => { b!.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })); frames(60, 50) }
    go(button(root, 'BASKET'), 'Enter')
    for (let i = 0; i < 4; i++) { const t = button(root, 'TABLE', 'PART'); if (!t) break; go(t, 'Enter') }
    const f = button(root, 'FOLDED')
    go(f, ' '); go(f, ' ')
  }
  const lastDraw = (perDraw: number[]) => perDraw[perDraw.length - 1]

  it('retires a ghost that is already flying when the system turns reduced motion on', () => {
    let onChange: ((e: { matches: boolean }) => void) | undefined
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: false, media: q, addEventListener: (_t: string, fn: typeof onChange) => { if (q.includes('reduced-motion')) onChange = fn }, removeEventListener: () => {} }))
    const perDraw = recordEllipses()
    let ghosts = 0
    const root = mount()
    const h = startLaundry(root, { text: () => ({ ...TEXT, get ghost() { ghosts++; return 'GHOST' } }), random: seeded(3), forceGhost: true })
    frames(60, 50)
    const bare = lastDraw(perDraw)
    for (let i = 0; i < 4 && !ghosts; i++) playOne(root)
    expect(ghosts).toBeGreaterThanOrEqual(1)
    frames(60, 50) // the cloth drapes and the ghost takes off
    expect(lastDraw(perDraw)).toBeGreaterThan(bare) // the ghost's eyes are on screen
    onChange!({ matches: true })
    frames(80, 50) // the retire timer, the fade, and the idle loop
    expect(lastDraw(perDraw)).toBe(bare)
    expect(queue).toHaveLength(0)
    h.stop()
  })

  it('still retires a ghost born under reduced motion', () => {
    stubMotion(true)
    const perDraw = recordEllipses()
    let ghosts = 0
    const root = mount()
    const h = startLaundry(root, { text: () => ({ ...TEXT, get ghost() { ghosts++; return 'GHOST' } }), random: seeded(3), forceGhost: true })
    frames()
    const bare = lastDraw(perDraw)
    for (let i = 0; i < 4 && !ghosts; i++) stackOne(root)
    expect(ghosts).toBeGreaterThanOrEqual(1)
    frames(80, 50)
    expect(lastDraw(perDraw)).toBe(bare)
    expect(queue).toHaveLength(0)
    h.stop()
  })
})

describe('laundry tween loop', () => {
  const tw = (o: Record<string, number>, to: number, dur: number, done?: () => void) => ({ o, from: { x: o.x }, to: { x: to }, t0: 0, dur, ease: (t: number) => t, done })
  it('steps every tween in the frame, even when a finished tween\'s callback cancels an earlier one', () => {
    const early = { x: 0 }, a = { x: 0 }, b = { x: 0 }
    const list = [tw(early, 100, 1000), tw(a, 10, 100, () => { list.splice(0, 1) /* what a done callback's tween() does to an earlier tween on the same key */ }), tw(b, 10, 100)]
    runTweenList(list, 100)
    expect(a.x).toBe(10)
    expect(b.x).toBe(10) // not left for the next frame
    expect(list).toHaveLength(0)
  })

  it('runs a finished tween\'s callback after the frame\'s stepping, in order', () => {
    const calls: string[] = []
    const list = [tw({ x: 0 }, 1, 10, () => calls.push('first')), tw({ x: 0 }, 1, 10, () => calls.push('second'))]
    runTweenList(list, 10)
    expect(calls).toEqual(['first', 'second'])
  })
})

describe('laundry hint and layout', () => {
  it('marks the scene played after the first fold and clears it on Reset game', () => {
    stubMotion(true)
    const root = mount()
    const h = startLaundry(root, { text: () => TEXT, random: seeded(3) })
    frames()
    expect(root.classList.contains('laundry-played')).toBe(false)
    stackOne(root)
    expect(root.classList.contains('laundry-played')).toBe(true)
    h.restart()
    frames()
    expect(root.classList.contains('laundry-played')).toBe(false)
    h.stop()
  })

  it('keeps the basket beside the table on a phone-width pane, and stacks it only when the pane is much taller than wide', () => {
    stubMotion(true)
    const layoutAt = (w: number, h: number) => {
      const root = mount()
      root.getBoundingClientRect = () => ({ width: w, height: h, left: 0, top: 0, right: w, bottom: h, x: 0, y: 0, toJSON: () => ({}) })
      const g = startLaundry(root, { text: () => TEXT, random: seeded(2) })
      frames()
      const out = root.dataset.layout
      g.stop()
      return out
    }
    expect(layoutAt(272, 358)).toBe('landscape')
    expect(layoutAt(240, 480)).toBe('portrait')
  })
})

describe('laundry geometry', () => {
  it('reads the ghost from the product mark: one body outline and two eyes on its right side', () => {
    expect(GHOST_MARK.body.length).toBeGreaterThan(50)
    // Two eyes, left then right: the ghost faces right, so the second eye sits further right.
    const [left, right] = GHOST_MARK.eyes
    expect(right[0]).toBeGreaterThan(left[0])
    for (const [x, y, rx, ry] of GHOST_MARK.eyes) {
      expect(x).toBeGreaterThan(0.5)
      expect(y).toBeGreaterThan(0.2)
      expect(y).toBeLessThan(0.6)
      expect(rx).toBeGreaterThan(0)
      expect(ry).toBeGreaterThan(rx)
    }
  })

  it('a fold turns the flap over the crease and keeps the area', () => {
    const towel: Layer[] = [{ pts: [[-60, -35], [60, -35], [60, 35], [-60, 35]], lines: [] }]
    const { stat, flap } = foldLayers(towel, { ax: 0, at: 20, dir: 1 }, -1)
    expect(area(stat[0].pts) + area(flap[0].pts)).toBeCloseTo(120 * 70)
    expect(Math.max(...flap[0].pts.map(p => p[0]))).toBeLessThanOrEqual(20)
  })
})
