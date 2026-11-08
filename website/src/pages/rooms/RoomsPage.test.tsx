/**
 * The room screen end to end against a faked gateway: the list and the open room, the
 * create form's two-to-six rule, the "wrapping up" line, and the rooms-off state.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider } from 'react-redux'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

import { store } from '../../store'
import { ApiError } from '../../api/apiError'
import type { RoomDetail, RoomMessage, RoomSummary } from '../../api/rooms'
import { i18nT } from '../../i18n/t'

const rooms = { list: vi.fn(), get: vi.fn(), create: vi.fn(), post: vi.fn(), remove: vi.fn() }
const members = vi.fn()
const kirocrewConfig = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    rooms: {
      list: (...a: unknown[]) => rooms.list(...a),
      get: (...a: unknown[]) => rooms.get(...a),
      create: (...a: unknown[]) => rooms.create(...a),
      post: (...a: unknown[]) => rooms.post(...a),
      remove: (...a: unknown[]) => rooms.remove(...a),
    },
    members: (...a: unknown[]) => members(...a),
    kirocrewConfig: (...a: unknown[]) => kirocrewConfig(...a),
  },
}))

import RoomsPage from '../RoomsPage'

const NAMES = ['Atlas', 'Bolt', 'Cleo', 'Dana', 'Eve', 'Fay', 'Gus']
const roster = NAMES.map((name) => ({ name, slug: name.toLowerCase(), slot_key: `member-${name}`, running: false }))
const LAB: RoomSummary = { id: 'a'.repeat(16), name: 'Lab', members: ['Atlas', 'Bolt'], created: 1, seq: 2 }
const NOTES: RoomSummary = { id: 'b'.repeat(16), name: 'Notes', members: ['Cleo', 'Dana'], created: 2, seq: 0 }

const message = (seq: number, from: string, text: string, depth = 0): RoomMessage => ({ seq, from, text, depth, ts: 1_700_000_000 + seq })
const detail = (room: RoomSummary, messages: RoomMessage[], has_more = false): RoomDetail => ({ ...room, messages, has_more })

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
  rooms.get.mockImplementation(async (id: string) => ({
    room: id === LAB.id
      ? detail(LAB, [message(1, '', 'What next?'), message(2, 'Atlas', 'I would start with the build.', 1)])
      : detail(NOTES, []),
  }))
  members.mockResolvedValue({ members: roster })
  kirocrewConfig.mockResolvedValue({ agent: {} })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('the room list and the open room', () => {
  it('lists the rooms as links and opens one with its log oldest first', async () => {
    const user = userEvent.setup()
    mount()
    const links = await screen.findAllByTestId('room-list-item')
    expect(links.map((l) => l.getAttribute('href'))).toEqual([`/rooms/${LAB.id}`, `/rooms/${NOTES.id}`])
    await user.click(links[0])

    const log = await screen.findByTestId('room-log')
    const texts = within(log).getAllByRole('paragraph').map((p) => p.textContent)
    expect(texts).toEqual(['What next?', 'I would start with the build.'])
    expect(screen.getByTestId('room-title').textContent).toBe('Lab')
    // The user's line is labelled by the gateway's own "You", a Bot's by its name.
    expect(within(log).getByText(i18nT('pages.roomsPage.you'))).toBeTruthy()
    expect(within(log).getByText('Atlas')).toBeTruthy()
    expect(screen.getAllByTestId('room-member-chip')).toHaveLength(2)
  })

  it('pages in older messages on request and keeps them when the poll returns only the newest', async () => {
    const user = userEvent.setup()
    rooms.get.mockImplementation(async (_id: string, before?: number) => ({
      room: before
        ? detail(LAB, [message(1, '', 'first'), message(2, 'Atlas', 'second')], false)
        : detail(LAB, [message(3, '', 'third'), message(4, 'Bolt', 'fourth')], true),
    }))
    mount(`/rooms/${LAB.id}`)
    await user.click(await screen.findByTestId('room-load-earlier'))
    await waitFor(() => expect(screen.queryByTestId('room-load-earlier')).toBeNull())
    expect(rooms.get).toHaveBeenLastCalledWith(LAB.id, 3)
    const log = screen.getByTestId('room-log')
    expect(within(log).getAllByRole('paragraph').map((p) => p.textContent)).toEqual(['first', 'second', 'third', 'fourth'])
  })

  it('goes back to the list with the back arrow', async () => {
    const user = userEvent.setup()
    mount(`/rooms/${LAB.id}`)
    await screen.findByTestId('room-log')
    await user.click(screen.getByTestId('room-back'))
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

  it('says a room that is gone is gone', async () => {
    rooms.get.mockRejectedValue(new ApiError(404, 'no such room', JSON.stringify({ code: 'unknown_room' })))
    mount(`/rooms/${LAB.id}`)
    expect(await screen.findByTestId('room-gone')).toBeTruthy()
  })

  it('shows a failed list with a Retry', async () => {
    rooms.list.mockRejectedValue(new ApiError(500, 'boom'))
    mount()
    expect(await screen.findByTestId('room-list-error')).toBeTruthy()
  })
})

describe('sending', () => {
  it('posts the user message and clears the box', async () => {
    const user = userEvent.setup()
    rooms.post.mockResolvedValue({ ok: true, message: message(3, '', 'hi'), woke: ['Atlas', 'Bolt'] })
    mount(`/rooms/${LAB.id}`)
    // The box is off until the room has loaded.
    await screen.findByTestId('room-log')
    const box = screen.getByTestId('room-message-input') as HTMLTextAreaElement
    await user.type(box, 'hi{Enter}')
    await waitFor(() => expect(rooms.post).toHaveBeenCalledWith(LAB.id, 'hi'))
    await waitFor(() => expect(box.value).toBe(''))
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
  it('shows the notice, disables New room and the composer, and still reads the log', async () => {
    kirocrewConfig.mockResolvedValue({ agent: { group_rooms: false } })
    mount(`/rooms/${LAB.id}`)
    expect(await screen.findByTestId('rooms-off-notice')).toBeTruthy()
    expect((screen.getByTestId('room-new') as HTMLButtonElement).disabled).toBe(true)
    expect((await screen.findByTestId('room-message-input') as HTMLTextAreaElement).disabled).toBe(true)
    expect(await screen.findByTestId('room-log')).toBeTruthy()
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

describe('the create form: two to six Bots', () => {
  async function openForm() {
    const user = userEvent.setup()
    mount()
    await screen.findAllByTestId('room-list-item')
    await user.click(screen.getByTestId('room-new'))
    const dialog = await screen.findByTestId('create-room-dialog')
    return { user, dialog }
  }
  const submit = () => screen.getByTestId('room-create-submit') as HTMLButtonElement
  const pick = (user: ReturnType<typeof userEvent.setup>, name: string) => user.click(screen.getByTestId(`room-member-${name}`))

  it('blocks one Bot, allows two, and makes a seventh unpickable', async () => {
    const { user } = await openForm()
    await user.type(screen.getByTestId('room-name-input'), 'Planning')
    expect(submit().disabled).toBe(true)

    await pick(user, 'Atlas')
    expect(submit().disabled).toBe(true)
    expect(screen.getByTestId('room-create-hint').textContent).toBe(i18nT('pages.roomsPage.need_more_members', { min: 2 }))

    await pick(user, 'Bolt')
    expect(submit().disabled).toBe(false)

    for (const name of ['Cleo', 'Dana', 'Eve', 'Fay']) await pick(user, name)
    expect(screen.getByTestId('room-member-count').textContent).toBe(
      i18nT('pages.roomsPage.members_selected', { selected: 6, min: 2, max: 6 }),
    )
    const seventh = screen.getByTestId('room-member-Gus') as HTMLInputElement
    expect(seventh.disabled).toBe(true)
    await user.click(seventh)
    expect(seventh.checked).toBe(false)
    expect(submit().disabled).toBe(false)
    expect(screen.getByTestId('room-member-full')).toBeTruthy()
    // A picked Bot can still be taken out of a full room.
    expect((screen.getByTestId('room-member-Fay') as HTMLInputElement).disabled).toBe(false)
  })

  it('needs a name', async () => {
    const { user } = await openForm()
    await pick(user, 'Atlas')
    await pick(user, 'Bolt')
    expect(submit().disabled).toBe(true)
    await user.type(screen.getByTestId('room-name-input'), 'x')
    expect(submit().disabled).toBe(false)
  })

  it('creates the room with the picked names and opens it', async () => {
    const created: RoomSummary = { id: 'c'.repeat(16), name: 'Planning', members: ['Atlas', 'Cleo'], created: 3, seq: 0 }
    rooms.create.mockResolvedValue({ ok: true, room: created })
    const { user } = await openForm()
    await user.type(screen.getByTestId('room-name-input'), '  Planning ')
    await pick(user, 'Atlas')
    await pick(user, 'Cleo')
    await user.click(submit())
    await waitFor(() => expect(rooms.create).toHaveBeenCalledWith({ name: 'Planning', members: ['Atlas', 'Cleo'] }))
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe(`/rooms/${created.id}`))
  })

  it('shows the gateway refusal in the dialog and keeps what was typed', async () => {
    rooms.create.mockRejectedValue(new ApiError(409, 'a room named "Lab" already exists', JSON.stringify({ code: 'name_in_use' })))
    const { user } = await openForm()
    await user.type(screen.getByTestId('room-name-input'), 'Lab')
    await pick(user, 'Atlas')
    await pick(user, 'Bolt')
    await user.click(submit())
    expect(await screen.findByTestId('room-create-error')).toBeTruthy()
    expect((screen.getByTestId('room-name-input') as HTMLInputElement).value).toBe('Lab')
    expect((screen.getByTestId('room-member-Atlas') as HTMLInputElement).checked).toBe(true)
  })

  it('lists only Bots that have a thread, and says so when there are none', async () => {
    members.mockResolvedValue({ members: [{ ...roster[0], slot_key: '' }] })
    const { dialog } = await openForm()
    expect(within(dialog).getByTestId('room-no-bots')).toBeTruthy()
    expect(within(dialog).queryByTestId('room-member-Atlas')).toBeNull()
  })

  it('closes on Escape without creating anything, and holds focus inside', async () => {
    const { user, dialog } = await openForm()
    expect(dialog.contains(document.activeElement)).toBe(true)
    await user.tab()
    expect(dialog.contains(document.activeElement)).toBe(true)
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByTestId('create-room-dialog')).toBeNull())
    expect(rooms.create).not.toHaveBeenCalled()
  })
})

describe('deleting a room', () => {
  it('asks first, then deletes and returns to the list', async () => {
    const user = userEvent.setup()
    rooms.remove.mockResolvedValue({ ok: true })
    mount(`/rooms/${LAB.id}`)
    await screen.findByTestId('room-log')
    await user.click(screen.getByTestId('room-delete'))
    expect(rooms.remove).not.toHaveBeenCalled()
    await user.click(await screen.findByRole('button', { name: i18nT('pages.roomsPage.delete') }))
    await waitFor(() => expect(rooms.remove).toHaveBeenCalledWith(LAB.id))
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/rooms'))
  })
})
