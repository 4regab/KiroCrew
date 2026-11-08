/**
 * The Bot event row in a Bot's own chat: one quiet line collapsed, a button with
 * `aria-expanded` that opens the text, plain text only, a muted refusal with nothing to open.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { i18nT } from '../../i18n/t'
import type { ChatMessage } from '../../types'
import { isBotEventRow, isCrewmateChatRow, isCrewmateSpeech, awaitingCrewmateReply } from '../../components/chat/crewmateBubbles'
import { isSystemNoticeRow, SystemNoticeRow } from './CompactionCard'
import { readBotEvent } from './botEvent'

vi.mock('../../api/client', () => ({
  api: { members: async () => ({ members: [{ name: 'Four', slug: 'four', slot_key: 'member-four', running: false }] }) },
}))

const row = (event: Record<string, unknown>, extra: Partial<ChatMessage> = {}): ChatMessage =>
  ({ role: 'assistant', content: 'English fallback', cls: 'msg msg-a', meta: { kind: 'bot_event', bot_event: event }, ...extra }) as ChatMessage

function show(message: ChatMessage) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <SystemNoticeRow message={message} disclosureKey="k" />
    </QueryClientProvider>,
  )
}

afterEach(cleanup)

const SENT = { type: 'sent', me: 'Ace', peer: 'Four', text: 'Can you review PR 7?\nIt is small.', queued: false }

describe('the Bot event card', () => {
  it('is one line collapsed: who sent what to whom, and the first line of the text', () => {
    show(row(SENT))
    const toggle = screen.getByTestId('bot-event-toggle')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(toggle.textContent).toContain(i18nT('pages.chat.botEventCard.sent', { me: 'Ace', peer: 'Four' }))
    expect(toggle.textContent).toContain('Can you review PR 7?')
    expect(toggle.textContent).not.toContain('It is small.')
    expect(screen.queryByTestId('bot-event-body')).toBeNull()
  })

  it('opens to the full text with the button, with Enter and with Space', async () => {
    const user = userEvent.setup()
    show(row(SENT))
    const toggle = screen.getByTestId('bot-event-toggle')
    await user.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByTestId('bot-event-body').textContent).toContain('It is small.')
    toggle.focus()
    await user.keyboard('{Enter}')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    await user.keyboard(' ')
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
  })

  it('shows the peer text as plain text: markup and markdown stay literal', async () => {
    const user = userEvent.setup()
    show(row({ ...SENT, text: '<img src=x onerror=alert(1)> **bold** [a](http://x.test)' }))
    await user.click(screen.getByTestId('bot-event-toggle'))
    const body = screen.getByTestId('bot-event-body')
    expect(body.querySelector('img, strong, a')).toBeNull()
    expect(body.textContent).toContain('<img src=x onerror=alert(1)> **bold** [a](http://x.test)')
  })

  it('says a busy Bot reads a queued message when its turn ends', async () => {
    const user = userEvent.setup()
    show(row({ ...SENT, queued: true }))
    await user.click(screen.getByTestId('bot-event-toggle'))
    expect(screen.getByTestId('bot-event-body').textContent).toContain(i18nT('pages.chat.botEventCard.queued_note', { peer: 'Four' }))
  })

  it('draws a received message as "Message from Four"', () => {
    show(row({ type: 'received', me: 'Ace', peer: 'Four', text: 'Yes, after lunch.', hop: 1 }))
    expect(screen.getByTestId('bot-event-toggle').textContent).toContain(i18nT('pages.chat.botEventCard.received', { peer: 'Four' }))
  })

  it('draws a post and a room wake, and lists who said what with the user as You', async () => {
    const user = userEvent.setup()
    show(row({ type: 'room_post', me: 'Ace', room: 'Office', text: 'Standup in five.' }))
    expect(screen.getByTestId('bot-event-toggle').textContent).toContain(i18nT('pages.chat.botEventCard.room_post', { me: 'Ace', room: 'Office' }))
    cleanup()
    show(row({ type: 'room_wake', me: 'Ace', room: 'Office', messages: [{ from: '', text: 'hey there' }, { from: 'Four', text: 'hi' }], omitted: 3 }))
    const toggle = screen.getByTestId('bot-event-toggle')
    expect(toggle.textContent).toContain(i18nT('pages.chat.botEventCard.room_wake', { room: 'Office', n: 5 }))
    await user.click(toggle)
    const list = screen.getByTestId('bot-event-messages')
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      `${i18nT('pages.roomsPage.you')}hey there`,
      'Fourhi',
      i18nT('pages.chat.botEventCard.older_not_shown', { n: 3 }),
    ])
  })

  it('draws a refusal as a muted line with the reason and nothing to open', () => {
    show(row({ type: 'refused', me: 'Ace', peer: 'Four', room: '', reason: 'rate_limited' }))
    const line = screen.getByTestId('bot-event-refused')
    expect(line.textContent).toContain(
      i18nT('pages.chat.botEventCard.refused_bot', { peer: 'Four', reason: i18nT('pages.chat.botEventCard.reason_rate_limited') }),
    )
    expect(screen.queryByRole('button')).toBeNull()
    cleanup()
    show(row({ type: 'refused', me: 'Ace', peer: '', room: 'Office', reason: 'a_code_from_the_future' }))
    expect(screen.getByTestId('bot-event-refused').textContent).toContain(
      i18nT('pages.chat.botEventCard.refused_room', { room: 'Office', reason: i18nT('pages.chat.botEventCard.reason_other') }),
    )
  })

  it('falls back to the English line when the payload does not validate', () => {
    show(row({ type: 'sent', me: 'Ace' }))
    expect(screen.queryByTestId('bot-event-card')).toBeNull()
    expect(screen.getByText('English fallback')).toBeTruthy()
    expect(readBotEvent({ meta: { bot_event: 'junk' } })).toBeNull()
  })

  it('is read from a live row (kind on the row) as from a reloaded one (kind in meta)', () => {
    const live = { role: 'assistant', content: 'x', cls: '', kind: 'bot_event', meta: { bot_event: SENT } } as ChatMessage
    show(live)
    expect(screen.getByTestId('bot-event-card').getAttribute('data-event')).toBe('sent')
  })
})

describe('a Bot chat and the Bot event rows', () => {
  const event = row(SENT)

  it('draws the event row and no other system notice, and does not count it as speech', () => {
    expect(isSystemNoticeRow(event)).toBe(true)
    expect(isBotEventRow(event)).toBe(true)
    expect(isCrewmateSpeech(event)).toBe(false)
    expect(isCrewmateChatRow(event)).toBe(true)
    const compaction = { role: 'assistant', content: 'Compacted.', cls: '', meta: { kind: 'compaction' } } as ChatMessage
    expect(isCrewmateChatRow(compaction)).toBe(false)
    const reload = { role: 'assistant', content: 'Reloaded.', cls: '', meta: { kind: 'session_reload' } } as ChatMessage
    expect(isCrewmateChatRow(reload)).toBe(false)
  })

  it('does not end the typing bubble: a Bot that sent a message is still working on the reply', () => {
    const sent = { role: 'user', content: 'ask Four', cls: '', meta: { sendId: 's1' } } as ChatMessage
    expect(awaitingCrewmateReply([sent, event], 's1')).toBe(true)
    const reply = { role: 'assistant', content: 'Four says yes.', cls: '' } as ChatMessage
    expect(awaitingCrewmateReply([sent, event, reply], 's1')).toBe(false)
  })
})
