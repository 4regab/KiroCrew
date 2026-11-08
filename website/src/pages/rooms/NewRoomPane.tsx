/**
 * "New group chat": a `To:` field. Typing filters the Bots that have a thread; the
 * highlighted row says "Add to group chat", and a picked Bot becomes a chip. Two to six
 * Bots make a room, which the gateway names after its members (the name can be changed
 * later from the room's details). The field is a combobox: arrow keys move, Enter picks,
 * Backspace on an empty field removes the last chip, Escape leaves. A refusal shows below
 * and keeps the picks.
 */
import { useId, useMemo, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { ArrowLeft, X } from 'lucide-react'
import { api } from '../../api/client'
import type { MemberRosterRow } from '../../api/client/agents'
import { errorCode } from '../../api/cards'
import { MAX_ROOM_MEMBERS, MIN_ROOM_MEMBERS, type RoomSummary } from '../../api/rooms'
import CrewAvatar from '../../components/CrewAvatar'
import ErrorNotice from '../../components/ErrorNotice'
import { Btn, IconButton } from '../../components/ui'
import { useImeGuard } from '../../hooks/useImeGuard'
import { i18nT } from '../../i18n/t'
import { createBlock, errText, toggleMember } from './roomLogic'

export default function NewRoomPane({ roster, rosterFailed, roomsOff, onCreated, onClose, onRoomsOff }: {
  roster: readonly MemberRosterRow[]
  rosterFailed: boolean
  roomsOff: boolean
  onCreated: (room: RoomSummary) => void
  onClose: () => void
  /** The gateway answered `group_rooms_off`: the page shows its notice. */
  onRoomsOff: () => void
}) {
  const [picked, setPicked] = useState<string[]>([])
  const [text, setText] = useState('')
  const [active, setActive] = useState(0)
  const [problem, setProblem] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const ime = useImeGuard()
  const listId = useId()

  const label = (name: string) => roster.find((r) => r.name === name)?.display_name || name
  const full = picked.length >= MAX_ROOM_MEMBERS
  const options = useMemo(() => {
    const q = text.trim().toLowerCase()
    return roster.filter(
      (r) => !picked.includes(r.name) && (!q || r.name.toLowerCase().includes(q) || (r.display_name ?? '').toLowerCase().includes(q)),
    )
  }, [roster, picked, text])
  const open = !full && !rosterFailed && options.length > 0
  const at = Math.min(active, Math.max(options.length - 1, 0))
  const block = createBlock(picked)

  const create = useMutation({
    mutationFn: () => api.rooms.create({ members: picked }),
    onSuccess: ({ room }) => onCreated(room),
    onError: (e) => {
      if (errorCode(e) === 'group_rooms_off') onRoomsOff()
      setProblem(errText(e))
    },
  })
  const canCreate = !block && !roomsOff && !create.isPending

  const pick = (name: string) => {
    setPicked(toggleMember(picked, name))
    setText('')
    setActive(0)
    input.current?.focus()
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3" data-testid="new-room">
      <header className="flex min-h-11 items-start gap-2">
        <IconButton
          aria-label={i18nT('pages.roomsPage.back_to_rooms')}
          onClick={onClose}
          className="min-h-11 min-w-11 shrink-0 rounded-full md:hidden"
          data-testid="new-room-back"
        >
          <ArrowLeft size={18} aria-hidden="true" />
        </IconButton>
        <div className="relative min-w-0 flex-1">
          <div className="flex min-h-11 flex-wrap items-center gap-1.5 rounded-2xl border border-border bg-bg-elevated px-3 py-1.5 focus-within:border-border-strong">
            <span className="text-sm text-muted">{i18nT('pages.roomsPage.to_label')}</span>
            {picked.map((name) => (
              <span key={name} className="flex items-center gap-1.5 rounded-full bg-bg-hover py-0.5 pl-1 pr-1 text-sm text-text-strong" data-testid="new-room-chip">
                <CrewAvatar seed={name} avatar={roster.find((r) => r.name === name)?.avatar} size={20} />
                <span className="max-w-[10rem] truncate">{label(name)}</span>
                <IconButton
                  aria-label={i18nT('pages.roomsPage.remove_member', { name: label(name) })}
                  onClick={() => pick(name)}
                  className="size-6 rounded-full p-0"
                >
                  <X size={12} aria-hidden="true" />
                </IconButton>
              </span>
            ))}
            <input
              ref={input}
              value={text}
              autoFocus
              disabled={roomsOff}
              role="combobox"
              aria-expanded={open}
              aria-controls={open ? listId : undefined}
              aria-autocomplete="list"
              aria-activedescendant={open ? `${listId}-${at}` : undefined}
              aria-label={i18nT('pages.roomsPage.to_placeholder')}
              placeholder={picked.length === 0 ? i18nT('pages.roomsPage.to_placeholder') : ''}
              className="min-h-8 min-w-[8rem] flex-1 border-0 bg-transparent p-0 text-sm text-text outline-hidden placeholder:text-muted"
              onChange={(e) => { setText(e.target.value); setActive(0) }}
              {...ime.bindComposition()}
              onKeyDown={(e) => {
                if (e.key === 'ArrowDown' && open) { e.preventDefault(); setActive((at + 1) % options.length); return }
                if (e.key === 'ArrowUp' && open) { e.preventDefault(); setActive((at - 1 + options.length) % options.length); return }
                if (e.key === 'Enter') {
                  if (!ime.claimEnter(e)) return
                  e.preventDefault()
                  if (open) pick(options[at].name)
                  else if (canCreate) create.mutate()
                  return
                }
                if (e.key === 'Backspace' && text === '' && picked.length > 0) { setPicked(picked.slice(0, -1)); return }
                if (e.key === 'Escape') { e.preventDefault(); onClose() }
              }}
              data-testid="new-room-input"
            />
          </div>
          {open && (
            <div
              id={listId}
              role="listbox"
              aria-label={i18nT('pages.roomsPage.members_label')}
              className="absolute left-0 top-full z-10 mt-1 max-h-72 w-full overflow-y-auto rounded-2xl border border-border bg-bg-elevated p-1 shadow-lg"
              data-testid="new-room-options"
            >
              {options.map((row, i) => (
                <div
                  key={row.name}
                  id={`${listId}-${i}`}
                  role="option"
                  tabIndex={-1}
                  aria-selected={i === at}
                  onMouseDown={(e) => { e.preventDefault(); pick(row.name) }}
                  onMouseEnter={() => setActive(i)}
                  className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-xl px-3 text-sm ${
                    i === at ? 'bg-bg-hover text-text-strong' : 'text-text'
                  }`}
                  data-testid={`new-room-option-${row.name}`}
                >
                  <CrewAvatar seed={row.name} avatar={row.avatar} size={24} className="shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{row.display_name || row.name}</span>
                  {i === at && <span className="shrink-0 text-[13px] text-muted">{i18nT('pages.roomsPage.add_to_group')}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
        <Btn
          primary
          onClick={() => create.mutate()}
          disabled={!canCreate}
          className="min-h-11"
          data-testid="new-room-create"
        >
          {i18nT('pages.roomsPage.create')}
        </Btn>
      </header>
      <div className="flex flex-col gap-2 px-1">
        <p className="m-0 text-[13px] text-muted" role="status" data-testid="new-room-hint">
          {rosterFailed
            ? i18nT('pages.roomsPage.members_load_failed')
            : roster.length === 0
              ? i18nT('pages.roomsPage.no_bots')
              : full
                ? i18nT('pages.roomsPage.members_full', { max: MAX_ROOM_MEMBERS })
                : picked.length < MIN_ROOM_MEMBERS
                  ? i18nT('pages.roomsPage.new_room_hint', { min: MIN_ROOM_MEMBERS, max: MAX_ROOM_MEMBERS })
                  : ''}
        </p>
        {roomsOff && <p className="m-0 text-[13px] text-danger-text" data-testid="room-create-off">{i18nT('pages.roomsPage.rooms_off_short')}</p>}
        {/* No hand-off: the picks stay and the refusal is shown beside them, so a retry loses nothing. */}
        <ErrorNotice message={problem} onDismiss={() => setProblem('')} testId="room-create-error" />
      </div>
    </div>
  )
}
