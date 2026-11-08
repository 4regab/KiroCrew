/**
 * Behaviour check + screenshots for the Bot event rows in a Bot's own chat.
 *
 * Mounts the REAL Members page on a Bot's chat (`capture/members-page.html`), with
 * route-intercepted, gateway-free answers: no gateway, no Bots. The thread holds the
 * person's questions, the Bot's replies, one Bot event row of each kind (sent, received,
 * room post, room wake, two refusals) and a compaction notice, which a Bot's chat must
 * still hide. It drives 375, 768 and 1280 px and fails (exit 1) when a claim does not hold:
 *
 *   every width  six event rows, one line each, collapsed; the compaction notice is hidden;
 *                no sideways scroll; each row fits the screen; a row opens and closes with
 *                the keyboard (aria-expanded flips); the text inside is plain text;
 *                a refusal has nothing to open
 *   screenshots  bot-events-<width>-collapsed.png and -expanded.png
 *
 * Nothing in CI runs this file; the rules it leans on are unit-tested in
 * `src/pages/chat/BotEventCard.test.tsx`.
 *
 * Usage (in website/): node scripts/verify-bot-events.mjs [outDir]
 */
import { chromium } from 'playwright'
import { createServer } from 'vite'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { THREAD, makeCheck, routeRadarDm } from './lib/radar-dm-fixture.mjs'

const OUT = resolve(process.argv[2] || '../temp-screenshots/bot-events')
mkdirSync(OUT, { recursive: true })

const at = (s) => `2026-10-05T07:41:${String(s).padStart(2, '0')}Z`
const event = (s, payload, content) => ({
  role: 'assistant', content, cls: 'msg msg-a', ts: at(s),
  meta: { mid: `ev${s}`, kind: 'bot_event', bot_event: { me: 'Radar', ...payload } },
})
const THREAD_WITH_EVENTS = [
  THREAD[0], THREAD[1], THREAD[2],
  event(1, { type: 'sent', peer: 'Fixer', queued: true, text: 'Can you open the PR for #16628?\nIt needs the <b>to_thread</b> fix, **not** a lock.' }, 'Radar sent a message to Fixer'),
  event(2, { type: 'received', peer: 'Fixer', hop: 1, text: 'Opened #16631 with the fix. CI is running.' }, 'Message from Fixer'),
  event(3, { type: 'room_post', room: 'Office', text: 'Nightly is green again.' }, 'Radar posted in Office'),
  event(4, { type: 'room_wake', room: 'Office', omitted: 2, messages: [{ from: '', text: 'hey there' }, { from: 'Fixer', text: 'Nice, thanks.' }] }, 'Group chat Office'),
  event(5, { type: 'refused', peer: 'Ghost', room: '', reason: 'unknown_bot' }, 'Not sent'),
  event(6, { type: 'refused', peer: 'Fixer', room: '', reason: 'rate_limited' }, 'Not sent'),
  { role: 'assistant', content: 'Context summary: everything before this point.', cls: 'msg msg-a', ts: at(7), meta: { mid: 'cmp1', kind: 'compaction' } },
  THREAD[3], THREAD[4],
]

const server = await createServer({
  configFile: './vite.config.ts',
  server: { host: '127.0.0.1', port: 5199, strictPort: false },
  logLevel: 'warn',
})
await server.listen()
const BASE = server.resolvedUrls?.local?.[0]?.replace(/\/$/, '') || `http://127.0.0.1:${server.config.server.port}`
const { LD_LIBRARY_PATH: _mise, ...browserEnv } = process.env
const browser = await chromium.launch({ env: browserEnv })
const state = { failures: 0 }
const check = makeCheck(state)

for (const [width, height, mobile] of [[375, 812, true], [768, 900, false], [1280, 720, false]]) {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 2, isMobile: mobile, hasTouch: mobile })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await routeRadarDm(page, { thread: THREAD_WITH_EVENTS })
  await page.goto(`${BASE}/capture/members-page.html?theme=dark&route=/members%3Fmember%3DRadar`)
  await page.waitForSelector('[data-capture-root]')
  await page.getByText('Good. And the dispatcher?').waitFor()
  const w = `[${width}]`

  const cards = page.getByTestId('bot-event-card')
  check(`${w} six event rows are drawn`, (await cards.count()) === 6)
  check(`${w} the compaction notice stays hidden`, (await page.getByText('Context summary').count()) === 0)
  check(`${w} the person's and the Bot's own words still draw`, (await page.getByText('Shard 1 went red twice').count()) === 1)
  check(`${w} every row is a single line collapsed`, await page.evaluate(() =>
    [...document.querySelectorAll('[data-testid="bot-event-card"]')].every((c) => c.getBoundingClientRect().height < 48)))
  check(`${w} no sideways scroll, each row fits the screen`, await page.evaluate(() =>
    document.documentElement.scrollWidth <= window.innerWidth + 1 &&
    [...document.querySelectorAll('[data-testid="bot-event-card"]')].every((c) => { const r = c.getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth })))
  const toggles = page.getByTestId('bot-event-toggle')
  check(`${w} four rows open and the two refusals do not`, (await toggles.count()) === 4 && (await page.getByTestId('bot-event-refused').count()) === 2)
  check(`${w} the refusal gives its reason`, (await page.getByTestId('bot-event-refused').first().textContent()).includes('no Bot or room has that name'))
  check(`${w} all start collapsed`, (await page.getByTestId('bot-event-body').count()) === 0 &&
    (await toggles.evaluateAll((els) => els.every((e) => e.getAttribute('aria-expanded') === 'false'))))
  await page.getByTestId('bot-event-card').first().scrollIntoViewIfNeeded()
  await page.screenshot({ path: resolve(OUT, `bot-events-${width}-collapsed.png`) })

  // keyboard: focus the first row, Enter opens, Space closes
  await toggles.first().focus()
  await page.keyboard.press('Enter')
  check(`${w} Enter opens a row`, (await toggles.first().getAttribute('aria-expanded')) === 'true')
  const body = (await page.getByTestId('bot-event-body').first().textContent()) ?? ''
  check(`${w} the text is plain: markup and markdown stay literal`,
    body.includes('<b>to_thread</b>') && body.includes('**not**') && (await page.getByTestId('bot-event-body').first().locator('b, strong').count()) === 0)
  await page.keyboard.press('Space')
  check(`${w} Space closes it`, (await toggles.first().getAttribute('aria-expanded')) === 'false')

  for (let i = 0; i < (await toggles.count()); i++) await toggles.nth(i).click()
  check(`${w} all four open`, (await page.getByTestId('bot-event-body').count()) === 4)
  check(`${w} the room wake lists who said what`, (await page.getByTestId('bot-event-messages').first().textContent()).includes('Fixer'))
  check(`${w} still no sideways scroll when open`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1))
  await page.getByTestId('bot-event-card').first().scrollIntoViewIfNeeded()
  await page.screenshot({ path: resolve(OUT, `bot-events-${width}-expanded.png`) })
  check(`${w} no page errors`, errors.length === 0)
  if (errors.length) console.log(errors.join('\n'))
  await ctx.close()
}
await browser.close()
await server.close()
if (state.failures) { console.error(`${state.failures} check(s) failed`); process.exit(1) }
console.log(`wrote ${OUT}`)
