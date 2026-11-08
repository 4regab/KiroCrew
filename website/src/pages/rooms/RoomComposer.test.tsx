import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { MemberRosterRow } from '../../api/client/agents'
import type { RoomMessage } from '../../api/rooms'
import { i18nT } from '../../i18n/t'
import RoomComposer from './RoomComposer'

const MEMBERS = ['Atlas', 'Bolt', 'Cleo']
const BY_NAME = new Map(MEMBERS.map((name) => [name, { name, slug: name, slot_key: name, running: false } as MemberRosterRow]))
const ATLAS_SAID: RoomMessage = { seq: 4, from: 'Atlas', text: 'Start with the build.', depth: 1, ts: 1 }

type Props = Parameters<typeof RoomComposer>[0]

function setup(onSend: Props['onSend'] = async () => true, extra: Partial<Props> = {}) {
  render(
    <RoomComposer
      roomName="Lab"
      members={MEMBERS}
      byName={BY_NAME}
      disabled={false}
      disabledReason="off"
      sending={false}
      replyTo={null}
      onCancelReply={() => {}}
      onSend={onSend}
      {...extra}
    />,
  )
  return screen.getByTestId('room-message-input') as HTMLTextAreaElement
}

afterEach(cleanup)

describe('RoomComposer pill', () => {
  it('names the room in its placeholder and has no character counter', () => {
    const box = setup()
    expect(box.placeholder).toBe(i18nT('pages.roomsPage.message_placeholder', { name: 'Lab' }))
    expect(screen.queryByTestId('room-char-count')).toBeNull()
  })
})

describe('RoomComposer @ list', () => {
  it('opens on @ with the room Bots and everyone, and filters as you type', async () => {
    const user = userEvent.setup()
    const box = setup()
    await user.type(box, '@')
    const options = screen.getAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual(['Atlas', 'Bolt', 'Cleo', i18nT('pages.roomsPage.mention_everyone')])
    await user.type(box, 'b')
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Bolt'])
  })

  it('picks with Enter into a chip, takes the @ out of the text, and keeps the focus in the box', async () => {
    const user = userEvent.setup()
    const box = setup()
    await user.type(box, 'hi @')
    await user.keyboard('{ArrowDown}')
    expect(screen.getAllByRole('option')[1].getAttribute('aria-selected')).toBe('true')
    expect(box.getAttribute('aria-activedescendant')).toBe(screen.getAllByRole('option')[1].id)
    await user.keyboard('{Enter}')
    expect(box.value).toBe('hi ')
    expect(screen.getByTestId('room-mention-chips').textContent).toContain('@Bolt')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(document.activeElement).toBe(box)
  })

  it('picks with Tab too, instead of moving focus away', async () => {
    const user = userEvent.setup()
    const box = setup()
    await user.type(box, '@c')
    await user.keyboard('{Tab}')
    expect(screen.getByTestId('room-mention-chips').textContent).toContain('@Cleo')
    expect(document.activeElement).toBe(box)
  })

  it('wraps around, picks everyone, offers a picked Bot no more, and Escape closes without picking', async () => {
    const user = userEvent.setup()
    const box = setup()
    await user.type(box, '@')
    await user.keyboard('{ArrowUp}{Enter}')
    expect(screen.getByTestId('room-mention-chips').textContent).toContain('@everyone')

    await user.type(box, '@')
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Atlas', 'Bolt', 'Cleo'])
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(box.value).toBe('@')
  })

  it('does not open for an address, or for a name no Bot in the room has', async () => {
    const user = userEvent.setup()
    const box = setup()
    await user.type(box, 'mail me@bolt.com')
    expect(screen.queryByRole('listbox')).toBeNull()
    await user.clear(box)
    await user.type(box, '@zz')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('sends the picked names in front of the text, and a chip can be taken out first', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn(async () => true)
    const box = setup(onSend)
    await user.type(box, '@')
    await user.keyboard('{Enter}')
    await user.type(box, '@')
    await user.keyboard('{Enter}')
    await user.click(screen.getByRole('button', { name: i18nT('pages.roomsPage.remove_mention', { name: 'Atlas' }) }))
    await user.type(box, 'look at this{Enter}')
    expect(onSend).toHaveBeenCalledWith('@Bolt look at this', null)
    await waitFor(() => expect(screen.queryByTestId('room-mention-chips')).toBeNull())
  })
})

describe('RoomComposer reply', () => {
  it('shows what is answered above the pill, can drop it, and sends the message with it', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn(async () => true)
    const onCancel = vi.fn()
    const box = setup(onSend, { replyTo: ATLAS_SAID, onCancelReply: onCancel })
    const bar = screen.getByTestId('room-reply-bar')
    expect(bar.textContent).toContain(i18nT('pages.roomsPage.replying_to', { name: 'Atlas' }))
    expect(bar.textContent).toContain('Start with the build.')
    expect(document.activeElement).toBe(box)
    await user.type(box, 'why{Enter}')
    expect(onSend).toHaveBeenCalledWith('why', ATLAS_SAID)
    await user.click(screen.getByTestId('room-reply-cancel'))
    expect(onCancel).toHaveBeenCalled()
  })
})

describe('RoomComposer sending', () => {
  it('sends on Enter, keeps a new line on Shift+Enter, and clears only after the gateway took it', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn(async () => true)
    const box = setup(onSend)
    await user.type(box, 'one{Shift>}{Enter}{/Shift}two')
    expect(box.value).toBe('one\ntwo')
    await user.keyboard('{Enter}')
    expect(onSend).toHaveBeenCalledWith('one\ntwo', null)
    await waitFor(() => expect(box.value).toBe(''))
  })

  it('keeps the draft when the send fails', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn(async () => false)
    const box = setup(onSend)
    await user.type(box, 'keep me{Enter}')
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1))
    expect(box.value).toBe('keep me')
  })

  it('will not send an empty message, and sends a very long one whole', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn(async () => true)
    const box = setup(onSend)
    const send = screen.getByTestId('room-send') as HTMLButtonElement
    expect(send.disabled).toBe(true)
    await user.type(box, '   ')
    expect(send.disabled).toBe(true)
    await user.clear(box)
    // paste, not type: 20,000 keystrokes would only test the keyboard
    const long = 'x'.repeat(20_000)
    await user.click(box)
    await user.paste(long)
    expect(send.disabled).toBe(false)
    await user.keyboard('{Enter}')
    await waitFor(() => expect(onSend).toHaveBeenCalledWith(long, null))
  })

  it('is off, with the reason as its placeholder, when rooms are turned off', () => {
    const box = setup(async () => true, { disabled: true })
    expect(box.disabled).toBe(true)
    expect(box.placeholder).toBe('off')
  })
})
