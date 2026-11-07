import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MAX_ROOM_MESSAGE_CHARS } from '../../api/rooms'
import { i18nT } from '../../i18n/t'
import RoomComposer from './RoomComposer'

const MEMBERS = ['Atlas', 'Bolt', 'Cleo']

function setup(onSend: (text: string) => Promise<boolean> = async () => true, extra: Partial<Parameters<typeof RoomComposer>[0]> = {}) {
  render(
    <RoomComposer
      members={MEMBERS}
      disabled={false}
      disabledReason="off"
      sending={false}
      onSend={onSend}
      {...extra}
    />,
  )
  return screen.getByTestId('room-message-input') as HTMLTextAreaElement
}

afterEach(cleanup)

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

  it('moves with the arrow keys, picks with Enter, and keeps the focus in the box', async () => {
    const user = userEvent.setup()
    const box = setup()
    await user.type(box, 'hi @')
    await user.keyboard('{ArrowDown}')
    expect(screen.getAllByRole('option')[1].getAttribute('aria-selected')).toBe('true')
    expect(box.getAttribute('aria-activedescendant')).toBe(screen.getAllByRole('option')[1].id)
    await user.keyboard('{Enter}')
    expect(box.value).toBe('hi @Bolt ')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(document.activeElement).toBe(box)
  })

  it('picks with Tab too, instead of moving focus away', async () => {
    const user = userEvent.setup()
    const box = setup()
    await user.type(box, '@c')
    await user.keyboard('{Tab}')
    expect(box.value).toBe('@Cleo ')
    expect(document.activeElement).toBe(box)
  })

  it('wraps around, picks everyone as the literal @everyone, and Escape closes without picking', async () => {
    const user = userEvent.setup()
    const box = setup()
    await user.type(box, '@')
    await user.keyboard('{ArrowUp}{Enter}')
    expect(box.value).toBe('@everyone ')

    await user.clear(box)
    await user.type(box, '@')
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
})

describe('RoomComposer sending', () => {
  it('sends on Enter, keeps a new line on Shift+Enter, and clears only after the gateway took it', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn(async () => true)
    const box = setup(onSend)
    await user.type(box, 'one{Shift>}{Enter}{/Shift}two')
    expect(box.value).toBe('one\ntwo')
    await user.keyboard('{Enter}')
    expect(onSend).toHaveBeenCalledWith('one\ntwo')
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

  it('will not send an empty message or one past the 2,000-character limit', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn(async () => true)
    const box = setup(onSend)
    const send = screen.getByTestId('room-send') as HTMLButtonElement
    expect(send.disabled).toBe(true)
    await user.type(box, '   ')
    expect(send.disabled).toBe(true)
    await user.clear(box)
    // paste, not type: 2,001 keystrokes would only test the keyboard
    await user.click(box)
    await user.paste('x'.repeat(MAX_ROOM_MESSAGE_CHARS + 1))
    expect(send.disabled).toBe(true)
    expect(box.getAttribute('aria-invalid')).toBe('true')
    await user.keyboard('{Enter}')
    expect(onSend).not.toHaveBeenCalled()
    // at the limit it goes
    await user.keyboard('{Backspace}')
    expect(send.disabled).toBe(false)
  })

  it('is off, with the reason as its placeholder, when rooms are turned off', () => {
    const box = setup(undefined, { disabled: true, disabledReason: i18nT('pages.roomsPage.rooms_off_short') })
    expect(box.disabled).toBe(true)
    expect(box.placeholder).toBe(i18nT('pages.roomsPage.rooms_off_short'))
    expect((screen.getByTestId('room-send') as HTMLButtonElement).disabled).toBe(true)
  })
})
