/**
 * Behaviour check + screenshots for the group chat rooms screen (`/rooms`).
 *
 * Runs the REAL built SPA (website/dist) with a stubbed, in-memory gateway for
 * `/api/rooms` and `/api/members`: no gateway, no Bots, no channels. It drives the
 * screen at 375, 768 and 1280 px and fails (exit 1) when a claim does not hold:
 *
 *   375   one column; list, then room, then back; create blocks one Bot and a seventh;
 *         the @ list works from the keyboard; the dialog traps focus and Escape closes it
 *   768   two panes; the open room and a half-typed message survive crossing 767 and 375
 *   1280  both panes scroll on their own; the page itself does not
 *   any   the log is a live region; the composer is a combobox; reduced motion draws
 *         the room in place
 *
 * Nothing in CI runs this file; the rules it leans on are unit-tested in
 * `src/pages/rooms/*.test.*`.
 *
 * Usage: npm run build && node scripts/verify-rooms-screen.mjs [outDir]
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { serveDist } from './lib/serve-dist.mjs'
import { logPageProblems, stubDashboardApi, json } from './lib/stub-dashboard-api.mjs'

const OUT = process.argv[2] || '../temp-screenshots/rooms-screen'

const NAMES = ['Atlas', 'Bolt', 'Cleo', 'Dana', 'Eve', 'Fay', 'Gus']
const ROSTER = NAMES.map((name) => ({
  name, slug: name.toLowerCase(), slot_key: `member-${name.toLowerCase()}`, running: false, source: 'kirocrew',
}))

const TEXTS = [
  'What should we ship first this week?',
  'I would start with the build fix, it blocks everything else.',
  'Agreed. I can review the patch once it is up.',
  'Do we need a migration for the new field?',
  'Not for this release. I checked the schema and nothing reads it yet.',
]

/** An in-memory gateway: the rooms the screen reads and writes. */
function makeStore() {
  let seq = 0
  const now = Math.floor(Date.now() / 1000)
  const room = (name, members, count) => {
    const id = (++seq).toString(16).padStart(16, '0')
    const messages = Array.from({ length: count }, (_, i) => ({
      seq: i + 1,
      from: i % 3 === 0 ? '' : members[i % members.length],
      text: `${TEXTS[i % TEXTS.length]} (${i + 1})`,
      depth: i % 3 === 0 ? 0 : 1,
      ts: now - (count - i) * 60,
    }))
    return { id, name, members, created: now, seq: count, messages }
  }
  const rooms = [room('Launch plan', ['Atlas', 'Bolt', 'Cleo'], 40), room('Standup', ['Dana', 'Eve'], 3)]
  for (let i = 1; i <= 14; i++) rooms.push(room(`Project room ${i}`, ['Atlas', 'Fay'], 0))
  const summary = ({ messages, ...rest }) => rest
  return { rooms, summary, nextId: () => (++seq).toString(16).padStart(16, '0'), posted: [], created: [] }
}

const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `  -- ${detail}`}`)
}

async function newPage(browser, base, store, { width, height, mobile = false, reducedMotion = 'no-preference' }) {
  const context = await browser.newContext({
    viewport: { width, height }, deviceScaleFactor: 2, isMobile: mobile, hasTouch: mobile, reducedMotion,
  })
  const page = await context.newPage()
  logPageProblems(page)
  const extra = async (path, route) => {
    const method = route.request().method()
    if (path === '/api/members') { await json(route, { members: ROSTER }); return true }
    if (path === '/api/rooms' && method === 'GET') { await json(route, { rooms: store.rooms.map(store.summary) }); return true }
    if (path === '/api/rooms' && method === 'POST') {
      const body = route.request().postDataJSON()
      if (!Array.isArray(body.members) || body.members.length < 2 || body.members.length > 6) {
        await json(route, { error: 'a room needs two to six Bots', code: 'too_few_members' }, 400); return true
      }
      const id = store.nextId()
      const created = { id, name: body.name, members: body.members, created: Date.now() / 1000, seq: 0, messages: [] }
      store.rooms.push(created); store.created.push(body)
      await json(route, { ok: true, room: store.summary(created) }, 201); return true
    }
    const one = /^\/api\/rooms\/([0-9a-f]{16})(\/messages)?$/.exec(path)
    if (one) {
      const found = store.rooms.find((r) => r.id === one[1])
      if (!found) { await json(route, { error: 'no such room', code: 'unknown_room' }, 404); return true }
      if (one[2] && method === 'POST') {
        const { message } = route.request().postDataJSON()
        const posted = { seq: ++found.seq, from: '', text: message, depth: 0, ts: Math.floor(Date.now() / 1000) }
        found.messages.push(posted); store.posted.push(message)
        await json(route, { ok: true, message: posted, woke: found.members }); return true
      }
      if (method === 'DELETE') { store.rooms.splice(store.rooms.indexOf(found), 1); await json(route, { ok: true }); return true }
      await json(route, { room: found }); return true
    }
    return false
  }
  await stubDashboardApi(page, { theme: 'dark', extra })
  return { context, page }
}

const visible = (page, id) => page.getByTestId(id).first().isVisible()
const noSideScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)

async function main() {
  const { srv, base } = await serveDist()
  mkdirSync(OUT, { recursive: true })
  const browser = await chromium.launch()
  const shots = []
  const shot = async (page, name) => { const path = `${OUT}/${name}.png`; await page.screenshot({ path }); shots.push(path) }

  try {
    // ── 375: phone ─────────────────────────────────────────────────────────
    {
      const store = makeStore()
      const { context, page } = await newPage(browser, base, store, { width: 375, height: 812, mobile: true })
      await page.goto(`${base}/rooms`, { waitUntil: 'domcontentloaded' })
      await page.getByTestId('room-list').waitFor({ timeout: 20000 })
      check('375: the list shows and the conversation pane does not', (await visible(page, 'room-list')) && !(await visible(page, 'rooms-conversation-pane')))
      check('375: no sideways scroll on the list', await noSideScroll(page))
      await shot(page, 'rooms-375-list')

      // create: blocks one Bot, allows two, blocks a seventh
      await page.getByTestId('room-new').click()
      await page.getByTestId('create-room-dialog').waitFor()
      await page.getByTestId('room-name-input').fill('Phone room')
      const submit = page.getByTestId('room-create-submit')
      await page.getByTestId('room-member-Atlas').check()
      check('375: one Bot cannot be created', await submit.isDisabled())
      await page.getByTestId('room-member-Bolt').check()
      check('375: two Bots can', await submit.isEnabled())
      for (const n of ['Cleo', 'Dana', 'Eve', 'Fay']) await page.getByTestId(`room-member-${n}`).check()
      check('375: a seventh Bot cannot be ticked', await page.getByTestId('room-member-Gus').isDisabled())
      check('375: the dialog fits the screen', await page.evaluate(() => {
        const r = document.querySelector('[data-testid="create-room-dialog"]').getBoundingClientRect()
        return r.left >= 0 && r.right <= window.innerWidth && r.top >= 0 && r.bottom <= window.innerHeight
      }))
      await shot(page, 'rooms-375-create')
      // focus stays inside the dialog while tabbing, and Escape closes it
      let escaped = false
      for (let i = 0; i < 16; i++) {
        await page.keyboard.press('Tab')
        if (!(await page.evaluate(() => !!document.activeElement?.closest('[data-testid="create-room-dialog"]')))) escaped = true
      }
      check('375: Tab never leaves the dialog', !escaped)
      await page.keyboard.press('Escape')
      await page.getByTestId('create-room-dialog').waitFor({ state: 'hidden' })
      check('375: Escape closes the dialog and nothing was created', store.created.length === 0)

      // open a room, use the @ list from the keyboard, send, go back
      await page.getByTestId('room-list-item').first().click()
      await page.getByTestId('room-log').waitFor()
      check('375: the room replaces the list, with a back arrow', !(await visible(page, 'room-list')) && (await visible(page, 'room-back')))
      check('375: no sideways scroll in the room', await noSideScroll(page))
      const box = page.getByTestId('room-message-input')
      check('375: the composer is a combobox and the log a live region',
        (await box.getAttribute('role')) === 'combobox' && (await page.getByRole('log').getAttribute('aria-live')) === 'polite')
      await box.click()
      await box.pressSequentially('hi @')
      await page.getByRole('listbox').waitFor()
      await page.keyboard.press('ArrowDown')
      check('375: the arrow key moves the highlight', (await page.getByRole('option').nth(1).getAttribute('aria-selected')) === 'true')
      await page.keyboard.press('Enter')
      check('375: Enter writes the name', (await box.inputValue()) === 'hi @Bolt ')
      await box.pressSequentially('please look')
      await shot(page, 'rooms-375-room')
      await page.keyboard.press('Enter')
      await page.getByText('hi @Bolt please look').waitFor()
      check('375: the message is posted, shown, and the box clears', store.posted[0] === 'hi @Bolt please look' && (await box.inputValue()) === '')
      await page.getByTestId('room-back').click()
      await page.getByTestId('room-list').waitFor()
      check('375: back returns to the list', !(await visible(page, 'rooms-conversation-pane')))
      await context.close()
    }

    // ── 768 and across the breakpoint ─────────────────────────────────────
    {
      const store = makeStore()
      const { context, page } = await newPage(browser, base, store, { width: 768, height: 900 })
      await page.goto(`${base}/rooms/${store.rooms[0].id}`, { waitUntil: 'domcontentloaded' })
      await page.getByTestId('room-log').waitFor({ timeout: 20000 })
      check('768: list and room side by side', (await visible(page, 'room-list')) && (await visible(page, 'rooms-conversation-pane')))
      check('768: no back arrow', !(await visible(page, 'room-back')))
      check('768: no sideways scroll', await noSideScroll(page))
      const box = page.getByTestId('room-message-input')
      await box.fill('half typed thought')
      await shot(page, 'rooms-768')
      // The shell switches its form (phone or desktop) a frame or two after the width changes,
      // so each read below waits for the list to reach the state the new width calls for.
      await page.setViewportSize({ width: 767, height: 900 })
      await page.getByTestId('room-list').waitFor({ state: 'hidden', timeout: 5000 })
      check('767: one column, still the same room', !(await visible(page, 'room-list')) && (await page.getByTestId('room-title').textContent()) === 'Launch plan')
      await page.setViewportSize({ width: 375, height: 812 })
      await page.setViewportSize({ width: 768, height: 900 })
      await page.getByTestId('room-list').waitFor({ state: 'visible', timeout: 5000 })
      const seen = { draft: await page.getByTestId('room-message-input').inputValue().catch(() => '(box gone)'), list: await visible(page, 'room-list'), title: await page.getByTestId('room-title').textContent().catch(() => '(title gone)') }
      check('768 again: the room and the draft survived 767 and 375', seen.draft === 'half typed thought' && seen.list && seen.title === 'Launch plan', JSON.stringify(seen))
      await context.close()
    }

    // ── 1280: both panes scroll apart ─────────────────────────────────────
    {
      const store = makeStore()
      const { context, page } = await newPage(browser, base, store, { width: 1280, height: 720 })
      await page.goto(`${base}/rooms/${store.rooms[0].id}`, { waitUntil: 'domcontentloaded' })
      await page.getByTestId('room-log').waitFor({ timeout: 20000 })
      await page.waitForFunction(() => {
        const s = document.querySelector('[data-testid="room-log-scroller"]')
        return s && s.scrollHeight > s.clientHeight
      })
      const metrics = () => page.evaluate(() => {
        const list = document.querySelector('[data-testid="rooms-list-pane"]')
        const log = document.querySelector('[data-testid="room-log-scroller"]')
        return {
          listScrolls: list.scrollHeight > list.clientHeight, logScrolls: log.scrollHeight > log.clientHeight,
          listTop: list.scrollTop, logTop: log.scrollTop, page: window.scrollY + document.documentElement.scrollTop,
        }
      })
      const before = await metrics()
      await shot(page, 'rooms-1280')
      check('1280: both panes have more than fits', before.listScrolls && before.logScrolls)
      check('1280: the log opens on the newest message', before.logTop > 0)
      await page.evaluate(() => { document.querySelector('[data-testid="rooms-list-pane"]').scrollTop = 200 })
      const afterList = await metrics()
      check('1280: scrolling the list leaves the log where it was', afterList.listTop > 0 && afterList.logTop === before.logTop)
      await page.evaluate(() => { document.querySelector('[data-testid="room-log-scroller"]').scrollTop = 0 })
      const afterLog = await metrics()
      check('1280: scrolling the log leaves the list where it was', afterLog.logTop === 0 && afterLog.listTop === afterList.listTop)
      check('1280: the page itself never scrolls', afterLog.page === 0)
      // the @ list from the keyboard works at this width too
      const box = page.getByTestId('room-message-input')
      await box.click()
      await box.pressSequentially('@e')
      check('1280: @e offers everyone only', (await page.getByRole('option').count()) === 1)
      await context.close()
    }

    // ── reduced motion ────────────────────────────────────────────────────
    {
      const store = makeStore()
      const { context, page } = await newPage(browser, base, store, { width: 375, height: 812, mobile: true, reducedMotion: 'reduce' })
      await page.goto(`${base}/rooms`, { waitUntil: 'domcontentloaded' })
      await page.getByTestId('room-list-item').first().waitFor({ timeout: 20000 })
      await page.getByTestId('room-list-item').first().click()
      const state = await page.getByTestId('rooms-conversation-pane').evaluate((el) => {
        const inner = el.firstElementChild
        const cs = getComputedStyle(inner)
        return { reduce: matchMedia('(prefers-reduced-motion: reduce)').matches, opacity: cs.opacity, transform: cs.transform }
      })
      check('reduced motion: the room is drawn in place, not sliding', state.reduce && state.opacity === '1' && (state.transform === 'none' || state.transform === 'matrix(1, 0, 0, 1, 0, 0)'), JSON.stringify(state))
      await context.close()
    }
  } finally {
    await browser.close()
    srv.close()
  }

  console.log('\nScreenshots:')
  for (const s of shots) console.log('  ' + s)
  const failed = results.filter((r) => !r.ok)
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
  process.exit(failed.length ? 1 : 0)
}

main().catch((e) => { console.error(e); process.exit(2) })
