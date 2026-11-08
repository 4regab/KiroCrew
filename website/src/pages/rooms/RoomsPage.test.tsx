/**
 * The room screen end to end against a faked gateway: the list (faces, names, newest line,
 * the row menu), the open room (bubbles, names, replies, the working line, details and
 * rename), the `To:` field that starts a group, the "wrapping up" line, and the rooms-off
 * state.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider } from 'react-redux'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

import { store } from '../../store'
import { ApiError } from '../../api/apiError'
import type { RoomDetail, RoomMessage, RoomSummary } from '../../api/rooms'
import { i18nT } from '../../i18n/t'

// happy-dom loops on Radix's real menu focus handling; the dashboard's shared mock stands in for it.
vi.mock('@radix-ui/react-context-menu', async () => await import('../../test/__mocks__/@radix-ui/react-context-menu'))

const rooms = { list: vi.fn(), get: vi.fn(), create: vi.fn(), rename: vi.fn(), post: vi.fn(), remove: vi.fn() }
const members = vi.fn()
const kirocrewConfig = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    rooms: {
      list: (...a: unknown[]) => rooms.list(...a),
      get: (...a: unknown[]) => rooms.get(...a),
      create: (...a: unknown[]) => rooms.create(...a),
      rename: (...a: unknown[]) => rooms.rename(...a),
      post: (...a: unknown[]) => rooms.post(...a),
      remove: (...a: unknown[]) => rooms.remove(...a),
    },
    members: (...a: unknown[]) => members(...a),
    kirocrewConfig: (...a: unknown[]) => kirocrewConfig(...a),
  },
}))

import RoomsPage from '../RoomsPage'

const NAMES = ['Atlas', 'Bolt', 'Cleo', 'Dana', 'Eve', 'Fay', 'Gus']
const row = (name: string, running = false) => ({ name, slug: name.toLowerCase(), slot_key: `member-${name}`, running })
const roster = NAMES.map((name) => row(name))
const LAB: RoomSummary = { id: 'a'.repeat(16), name: 'Lab', members: ['Atlas', 'Bolt'], created: 1, seq: 2 }
const NOTES: RoomSummary = { id: 'b'.repeat(16), name: 'Notes', members: ['Cleo', 'Dana'], created: 2, seq: 0 }

const message = (seq: number, from: string, text: string, depth = 0, extra: Partial<RoomMessage> = {}): RoomMessage => ({
  seq, from, text, depth, ts: 1_700_000_000 + seq, ...extra,
})
const detail = (room: RoomSummary, messages: RoomMessage[], has_more = false): RoomDetail => ({ ...room, messages, has_more })
const LAB_LOG = [message(1, '', 'What next?'), message(2, 'Atlas', 'I would start with the build.', 1)]

function Where() {
  return <span data-testid="where">{useLocation().pathname}</span>
}

function mount(path = '/rooms') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <Provider store={store}>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[path]}>
          <Where />
          <Routes>
            <Route path="/rooms/:roomId?" element={<RoomsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </Provider>,
  )
}

beforeEach(() => {
  rooms.list.mockResolvedValue({ rooms: [LAB, NOTES] })
  rooms.get.mockImplementation(async (id: string, page: { before?: number; limit?: number } = {}) => ({
    room: id === LAB.id
      ? detail(LAB, page.limit ? LAB_LOG.slice(-page.limit) : LAB_LOG)
      : detail(NOTES, []),
  }))
  members.mockResolvedValue({ members: roster })
  kirocrewConfig.mockResolvedValue({ agent: {} })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('the room list', () => {
  it('lists the rooms as links with a face, the name and the newest line', async () => {
    mount()
    const links = await screen.findAllByTestId('room-list-item')
    expect(links.map((l) => l.getAttribute('href'))).toEqual([`/rooms/${LAB.id}`, `/rooms/${NOTES.id}`])
    expect(within(links[0]).getByTestId('room-avatar')).toBeTruthy()
    expect(within(links[0]).getByText('Lab')).toBeTruthy()
    await waitFor(() => expect(within(links[0]).getByTestId('room-list-preview').textContent).toBe('I would start with the build.'))
    expect(rooms.get).toHaveBeenCalledWith(LAB.id, { limit: 1 })
    // an empty room is not asked for a preview
    expect(rooms.get).not.toHaveBeenCalledWith(NOTES.id, { limit: 1 })
  })

  it('offers Rename and Delete on a right-click, and Rename opens the details with the name to edit', async () => {
    const user = userEvent.setup()
    mount()
    const [first] = await screen.findAllByTestId('room-list-item')
    fireEvent.contextMenu(first)
    expect(await screen.findByTestId('room-menu-rename')).toBeTruthy()
    expect(screen.getByTestId('room-menu-delete')).toBeTruthy()
    await user.click(screen.getByTestId('room-menu-rename'))
    const input = (await screen.findByTestId('room-rename-input')) as HTMLInputElement
    expect(input.value).toBe('Lab')
  })

  it('deletes from the row menu after asking, and stays on the list', async () => {
    const user = userEvent.setup()
    rooms.remove.mockResolvedValue({ ok: true })
    mount()
    const [first] = await screen.findAllByTestId('room-list-item')
    fireEvent.contextMenu(first)
    await user.click(await screen.findByTestId('room-menu-delete'))
    expect(rooms.remove).not.toHaveBeenCalled()
    await user.click(await screen.findByRole('button', { name: i18nT('pages.roomsPage.delete') }))
    await waitFor(() => expect(rooms.remove).toHaveBeenCalledWith(LAB.id))
    expect(screen.getByTestId('where').textContent).toBe('/rooms')
  })

  it('shows the empty states', async () => {
    rooms.list.mockResolvedValue({ rooms: [] })
    mount()
    expect(await screen.findByTestId('rooms-empty')).toBeTruthy()

    cleanup()
    rooms.list.mockResolvedValue({ rooms: [NOTES] })
    mount(`/rooms/${NOTES.id}`)
    expect(await screen.findByTestId('room-empty')).toBeTruthy()
  })

  it('shows a failed list with a Retry', async () => {
    rooms.list.mockRejectedValue(new ApiError(500, 'boom'))
    mount()
    expect(await screen.findByTestId('room-list-error')).toBeTruthy()
  })
})

describe('the open room', () => {
  it('draws the user on the right and a Bot with its name, oldest first', async () => {
    const user = userEvent.setup()
    mount()
    await user.click((await screen.findAllByTestId('room-list-item'))[0])
    const log = await screen.findByTestId('room-log')
    expect(within(log).getAllByRole('paragraph').map((p) => p.textContent)).toEqual(['What next?', 'I would start with the build.'])
    expect(screen.getByTestId('room-message-user')).toBeTruthy()
    expect(within(log).getAllByTestId('room-message-name').map((n) => n.textContent)).toEqual(['Atlas'])
    expect(screen.getByTestId('room-title').textContent).toBe('Lab')
    expect((screen.getByTestId('room-message-input') as HTMLTextAreaElement).placeholder).toBe(
      i18nT('pages.roomsPage.message_placeholder', { name: 'Lab' }),
    )
  })

  it('names a Bot once for several messages in a row', async () => {
    rooms.get.mockResolvedValue({
      room: detail(LAB, [message(1, '', 'go'), message(2, 'Atlas', 'one', 1), message(3, 'Atlas', 'two', 1), message(4, 'Bolt', 'three', 1)]),
    })
    mount(`/rooms/${LAB.id}`)
    const log = await screen.findByTestId('room-log')
    expect(within(log).getAllByTestId('room-message-name').map((n) => n.textContent)).toEqual(['Atlas', 'Bolt'])
  })

  it('goes back to the list with the back arrow', async () => {
    const user = userEvent.setup()
    mount(`/rooms/${LAB.id}`)
    await screen.findByTestId('room-log')
    await user.click(screen.getByTestId('room-back'))
    expect(screen.getByTestId('where').textContent).toBe('/rooms')
  })

  it('says a room that is gone is gone', async () => {
    rooms.get.mockRejectedValue(new ApiError(404, 'no such room', JSON.stringify({ code: 'unknown_room' })))
    mount(`/rooms/${LAB.id}`)
    expect(await screen.findByTestId('room-gone')).toBeTruthy()
  })

  it('pages in older messages on request and keeps them when the poll returns only the newest', async () => {
    const user = userEvent.setup()
    rooms.get.mockImplementation(async (_id: string, page: { before?: number } = {}) => ({
      room: page.before
        ? detail(LAB, [message(1, '', 'first'), message(2, 'Atlas', 'second')], false)
        : detail(LAB, [message(3, '', 'third'), message(4, 'Bolt', 'fourth')], true),
    }))
    mount(`/rooms/${LAB.id}`)
    await user.click(await screen.findByTestId('room-load-earlier'))
    await waitFor(() => expect(screen.queryByTestId('room-load-earlier')).toBeNull())
    expect(rooms.get).toHaveBeenLastCalledWith(LAB.id, { before: 3 })
    const log = screen.getByTestId('room-log')
    expect(within(log).getAllByRole('paragraph').map((p) => p.textContent)).toEqual(['first', 'second', 'third', 'fourth'])
  })

  it('draws an @name in a message as a chip', async () => {
    rooms.get.mockResolvedValue({ room: detail(LAB, [message(1, '', '@Atlas please look')]) })
    mount(`/rooms/${LAB.id}`)
    const chip = await screen.findByTestId('room-mention-chip')
    expect(chip.textContent).toBe('@Atlas')
  })
})

describe('who is working', () => {
  it('shows one muted line for the working Bots of this room, with the first one\'s face', async () => {
    members.mockResolvedValue({ members: [row('Atlas', true), row('Bolt', true), row('Cleo', true)] })
    mount(`/rooms/${LAB.id}`)
    const line = await screen.findByTestId('room-working')
    expect(line.textContent).toContain(i18nT('pages.roomsPage.working_2', { a: 'Atlas', b: 'Bolt' }))
    // Cleo is working but is not in this room
    expect(line.textContent).not.toContain('Cleo')
  })

  it('says "and N more" past three, and shows nothing when nobody works', async () => {
    const big: RoomSummary = { ...LAB, members: ['Atlas', 'Bolt', 'Cleo', 'Dana', 'Eve'] }
    rooms.list.mockResolvedValue({ rooms: [big] })
    rooms.get.mockResolvedValue({ room: detail(big, LAB_LOG) })
    members.mockResolvedValue({ members: ['Atlas', 'Bolt', 'Cleo', 'Dana', 'Eve'].map((n) => row(n, true)) })
    mount(`/rooms/${LAB.id}`)
    expect((await screen.findByTestId('room-working')).textContent).toContain(
      i18nT('pages.roomsPage.working_n', { a: 'Atlas', b: 'Bolt', n: 3 }),
    )
    cleanup()
    members.mockResolvedValue({ members: roster })
    mount(`/rooms/${LAB.id}`)
    await screen.findByTestId('room-log')
    expect(screen.queryByTestId('room-working')).toBeNull()
  })
})

describe('sending and replying', () => {
  it('posts the user message and clears the box', async () => {
    const user = userEvent.setup()
    rooms.post.mockResolvedValue({ ok: true, message: message(3, '', 'hi'), woke: ['Atlas', 'Bolt'] })
    mount(`/rooms/${LAB.id}`)
    // The box is off until the room has loaded.
    await screen.findByTestId('room-log')
    const box = screen.getByTestId('room-message-input') as HTMLTextAreaElement
    await user.type(box, 'hi{Enter}')
    await waitFor(() => expect(rooms.post).toHaveBeenCalledWith(LAB.id, 'hi', undefined))
    await waitFor(() => expect(box.value).toBe(''))
  })

  it('replies to a message: quote above the box, the number goes with the post', async () => {
    const user = userEvent.setup()
    rooms.post.mockResolvedValue({ ok: true, message: message(3, '', 'why?'), woke: [] })
    mount(`/rooms/${LAB.id}`)
    const log = await screen.findByTestId('room-log')
    // the second message is Atlas's
    await user.click(within(log).getAllByTestId('room-reply')[1])
    const bar = screen.getByTestId('room-reply-bar')
    expect(bar.textContent).toContain('I would start with the build.')
    await user.type(screen.getByTestId('room-message-input'), 'why?{Enter}')
    await waitFor(() => expect(rooms.post).toHaveBeenCalledWith(LAB.id, 'why?', 2))
    await waitFor(() => expect(screen.queryByTestId('room-reply-bar')).toBeNull())
  })

  it('shows a reply with a snippet of what it answers', async () => {
    rooms.get.mockResolvedValue({
      room: detail(LAB, [
        message(1, 'Atlas', 'Start with the build.', 1),
        message(2, '', 'why?', 0, { reply_to: 1, quote: { from: 'Atlas', text: 'Start with the build.' } }),
      ]),
    })
    mount(`/rooms/${LAB.id}`)
    const quote = await screen.findByTestId('room-message-quote')
    expect(quote.textContent).toContain('Atlas')
    expect(quote.textContent).toContain('Start with the build.')
  })
})

describe('the "wrapping up" line', () => {
  it('shows once the newest message is three rounds deep, and not before', async () => {
    mount(`/rooms/${LAB.id}`)
    await screen.findByTestId('room-log')
    expect(screen.queryByTestId('room-wrapping-up')).toBeNull()

    cleanup()
    rooms.get.mockResolvedValue({ room: detail(LAB, [message(1, '', 'go'), message(2, 'Atlas', 'a', 1), message(3, 'Bolt', 'b', 3)]) })
    mount(`/rooms/${LAB.id}`)
    expect((await screen.findByTestId('room-wrapping-up')).textContent).toBe(i18nT('pages.roomsPage.wrapping_up'))
  })
})

describe('rooms turned off', () => {
  it('shows the notice, disables New group and the composer, and still reads the log', async () => {
    kirocrewConfig.mockResolvedValue({ agent: { group_rooms: false } })
    mount(`/rooms/${LAB.id}`)
    expect(await screen.findByTestId('rooms-off-notice')).toBeTruthy()
    expect((screen.getByTestId('room-new') as HTMLButtonElement).disabled).toBe(true)
    expect((await screen.findByTestId('room-message-input') as HTMLTextAreaElement).disabled).toBe(true)
    expect(await screen.findByTestId('room-log')).toBeTruthy()
    // no Reply while rooms are off
    expect(screen.queryAllByTestId('room-reply')).toHaveLength(0)
  })

  it('turns the screen off when a send comes back 403 group_rooms_off, and keeps the draft', async () => {
    const user = userEvent.setup()
    rooms.post.mockRejectedValue(new ApiError(403, 'off', JSON.stringify({ code: 'group_rooms_off' })))
    mount(`/rooms/${LAB.id}`)
    // The box is off until the room has loaded.
    await screen.findByTestId('room-log')
    const box = screen.getByTestId('room-message-input') as HTMLTextAreaElement
    await user.type(box, 'keep this{Enter}')
    expect(await screen.findByTestId('rooms-off-notice')).toBeTruthy()
    expect(await screen.findByTestId('room-send-error')).toBeTruthy()
    expect(box.value).toBe('keep this')
    expect((screen.getByTestId('room-new') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('the new group field: two to six Bots', () => {
  async function openField() {
    const user = userEvent.setup()
    mount()
    await screen.findAllByTestId('room-list-item')
    await user.click(screen.getByTestId('room-new'))
    const input = (await screen.findByTestId('new-room-input')) as HTMLInputElement
    return { user, input }
  }
  const create = () => screen.getByTestId('new-room-create') as HTMLButtonElement

  it('opens with the + button and also with Ctrl+Shift+N', async () => {
    mount()
    await screen.findAllByTestId('room-list-item')
    fireEvent.keyDown(window, { key: 'N', ctrlKey: true, shiftKey: true })
    expect(await screen.findByTestId('new-room-input')).toBeTruthy()
    expect(screen.getByTestId('where').textContent).toBe('/rooms/new')
  })

  it('lists the Bots with an "Add to group chat" hint on the active row, and filters as you type', async () => {
    const { user, input } = await openField()
    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(NAMES.length)
    expect(options[0].textContent).toContain(i18nT('pages.roomsPage.add_to_group'))
    expect(options[1].textContent).not.toContain(i18nT('pages.roomsPage.add_to_group'))
    await user.type(input, 'bo')
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([expect.stringContaining('Bolt')])
  })

  it('turns picked Bots into chips, blocks one Bot, allows two, and stops at six', async () => {
    const { user, input } = await openField()
    expect(create().disabled).toBe(true)
    await user.click(screen.getByTestId('new-room-option-Atlas'))
    expect(create().disabled).toBe(true)
    expect(screen.getByTestId('new-room-hint').textContent).toBe(i18nT('pages.roomsPage.new_room_hint', { min: 2, max: 6 }))
    await user.click(screen.getByTestId('new-room-option-Bolt'))
    expect(create().disabled).toBe(false)
    for (const name of ['Cleo', 'Dana', 'Eve', 'Fay']) await user.click(screen.getByTestId(`new-room-option-${name}`))
    expect(screen.getAllByTestId('new-room-chip')).toHaveLength(6)
    expect(screen.queryByTestId('new-room-options')).toBeNull()
    expect(screen.getByTestId('new-room-hint').textContent).toBe(i18nT('pages.roomsPage.members_full', { max: 6 }))
    expect(create().disabled).toBe(false)
    // Backspace on the empty field takes the last chip out
    await user.click(input)
    await user.keyboard('{Backspace}')
    expect(screen.getAllByTestId('new-room-chip')).toHaveLength(5)
  })

  it('picks with the keyboard, creates the room without a name and opens it', async () => {
    const created: RoomSummary = { id: 'c'.repeat(16), name: 'Atlas, Bolt', members: ['Atlas', 'Bolt'], created: 3, seq: 0 }
    rooms.create.mockResolvedValue({ ok: true, room: created })
    const { user, input } = await openField()
    await user.type(input, '{ArrowDown}{Enter}')
    await user.type(input, '{Enter}')
    expect(screen.getAllByTestId('new-room-chip')).toHaveLength(2)
    await user.click(create())
    await waitFor(() => expect(rooms.create).toHaveBeenCalledWith({ members: ['Bolt', 'Atlas'] }))
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe(`/rooms/${created.id}`))
  })

  it('shows the gateway refusal and keeps the picks', async () => {
    rooms.create.mockRejectedValue(new ApiError(404, 'no Bot named "Atlas"', JSON.stringify({ code: 'unknown_bot' })))
    const { user } = await openField()
    await user.click(screen.getByTestId('new-room-option-Atlas'))
    await user.click(screen.getByTestId('new-room-option-Bolt'))
    await user.click(create())
    expect(await screen.findByTestId('room-create-error')).toBeTruthy()
    expect(screen.getAllByTestId('new-room-chip')).toHaveLength(2)
  })

  it('lists only Bots that have a thread, and says so when there are none', async () => {
    members.mockResolvedValue({ members: [{ ...roster[0], slot_key: '' }] })
    await openField()
    expect(screen.queryByRole('option')).toBeNull()
    expect(screen.getByTestId('new-room-hint').textContent).toBe(i18nT('pages.roomsPage.no_bots'))
  })

  it('leaves on Escape without creating anything', async () => {
    const { user } = await openField()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/rooms'))
    expect(rooms.create).not.toHaveBeenCalled()
  })
})

describe('details and rename', () => {
  async function openDetails() {
    const user = userEvent.setup()
    mount(`/rooms/${LAB.id}`)
    await screen.findByTestId('room-log')
    await user.click(screen.getByTestId('room-title-pill'))
    const input = (await screen.findByTestId('room-rename-input')) as HTMLInputElement
    return { user, input }
  }

  it('opens from the header pill with the name to edit and the Bots in the room', async () => {
    const { input } = await openDetails()
    expect(input.value).toBe('Lab')
    expect(within(screen.getByTestId('room-details-members')).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Atlas', 'Bolt'])
    expect((screen.getByTestId('room-rename-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('renames the room, trimming the name, and closes', async () => {
    rooms.rename.mockResolvedValue({ ok: true, room: { ...LAB, name: 'Build crew' } })
    const { user, input } = await openDetails()
    await user.clear(input)
    await user.type(input, '  Build crew ')
    await user.click(screen.getByTestId('room-rename-save'))
    await waitFor(() => expect(rooms.rename).toHaveBeenCalledWith(LAB.id, 'Build crew'))
    await waitFor(() => expect(screen.queryByTestId('room-details')).toBeNull())
  })

  it('will not save an empty or an 81-character name', async () => {
    const { user, input } = await openDetails()
    await user.clear(input)
    expect((screen.getByTestId('room-rename-save') as HTMLButtonElement).disabled).toBe(true)
    await user.click(input)
    await user.paste('x'.repeat(81))
    expect((screen.getByTestId('room-rename-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows the gateway refusal and keeps what was typed', async () => {
    rooms.rename.mockRejectedValue(new ApiError(409, 'a room named "Notes" already exists', JSON.stringify({ code: 'name_in_use' })))
    const { user, input } = await openDetails()
    await user.clear(input)
    await user.type(input, 'Notes')
    await user.click(screen.getByTestId('room-rename-save'))
    expect(await screen.findByTestId('room-rename-error')).toBeTruthy()
    expect(input.value).toBe('Notes')
  })

  it('deletes from the details after asking, and returns to the list', async () => {
    rooms.remove.mockResolvedValue({ ok: true })
    const { user } = await openDetails()
    await user.click(screen.getByTestId('room-delete'))
    expect(rooms.remove).not.toHaveBeenCalled()
    const buttons = await screen.findAllByRole('button', { name: i18nT('pages.roomsPage.delete') })
    await user.click(buttons[buttons.length - 1])
    await waitFor(() => expect(rooms.remove).toHaveBeenCalledWith(LAB.id))
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/rooms'))
  })
})
