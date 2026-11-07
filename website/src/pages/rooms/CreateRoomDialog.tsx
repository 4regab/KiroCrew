/**
 * "New room": a name and two to six Bots. The form blocks a one-Bot or seven-Bot
 * room before asking the gateway, which refuses both anyway. A refusal shows in
 * the dialog and keeps what was typed.
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { MemberRosterRow } from '../../api/client/agents'
import { errorCode } from '../../api/cards'
import { MAX_ROOM_NAME_CHARS, MIN_ROOM_MEMBERS, type RoomSummary } from '../../api/rooms'
import ErrorNotice from '../../components/ErrorNotice'
import { Btn, Input } from '../../components/ui'
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { i18nT } from '../../i18n/t'
import MemberPicker from './MemberPicker'
import { createBlock, errText } from './roomLogic'

export default function CreateRoomDialog({ open, onOpenChange, roster, rosterFailed, roomsOff, onCreated, onRoomsOff }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  roster: readonly MemberRosterRow[]
  rosterFailed: boolean
  roomsOff: boolean
  onCreated: (room: RoomSummary) => void
  /** The gateway answered `group_rooms_off`: the page shows its notice. */
  onRoomsOff: () => void
}) {
  const [name, setName] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [problem, setProblem] = useState('')
  const block = createBlock(name, selected)

  const create = useMutation({
    mutationFn: () => api.rooms.create({ name: name.trim(), members: selected }),
    onSuccess: ({ room }) => {
      setName('')
      setSelected([])
      setProblem('')
      onCreated(room)
    },
    onError: (e) => {
      if (errorCode(e) === 'group_rooms_off') onRoomsOff()
      setProblem(errText(e))
    },
  })

  const hint =
    block === 'few' ? i18nT('pages.roomsPage.need_more_members', { min: MIN_ROOM_MEMBERS })
    : block === 'name' && name.length > 0 ? i18nT('pages.roomsPage.name_too_long', { max: MAX_ROOM_NAME_CHARS })
    : ''

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent maxWidth={480} data-testid="create-room-dialog">
        <DialogHeader>
          <DialogTitle>{i18nT('pages.roomsPage.create_title')}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <form
            id="create-room-form"
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              if (!block && !roomsOff && !create.isPending) create.mutate()
            }}
          >
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-text-strong">{i18nT('pages.roomsPage.name_label')}</span>
              <Input
                value={name}
                maxLength={MAX_ROOM_NAME_CHARS + 20}
                onChange={(e) => setName(e.target.value)}
                autoFocus
                data-testid="room-name-input"
              />
            </label>
            {rosterFailed ? (
              <ErrorNotice message={i18nT('pages.roomsPage.members_load_failed')} testId="room-roster-error" askAgent />
            ) : (
              <MemberPicker roster={roster} selected={selected} onChange={setSelected} />
            )}
            {hint && <p className="m-0 text-[13px] text-muted" role="status" data-testid="room-create-hint">{hint}</p>}
            {roomsOff && <p className="m-0 text-[13px] text-danger-text" data-testid="room-create-off">{i18nT('pages.roomsPage.rooms_off_short')}</p>}
            {/* No hand-off: the dialog holds an unsaved draft (the name and the picks), and the
                refusal is shown beside it so a retry loses nothing. */}
            <ErrorNotice message={problem} onDismiss={() => setProblem('')} testId="room-create-error" />
          </form>
        </DialogBody>
        <DialogFooter>
          <Btn onClick={() => onOpenChange(false)} data-testid="room-create-cancel">
            {i18nT('pages.roomsPage.cancel')}
          </Btn>
          <Btn
            primary
            type="submit"
            form="create-room-form"
            disabled={Boolean(block) || roomsOff || create.isPending}
            data-testid="room-create-submit"
          >
            {i18nT('pages.roomsPage.create')}
          </Btn>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
