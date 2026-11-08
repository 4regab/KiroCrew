/**
 * A room's details: its name, which can be changed here, and the Bots in it. Members
 * cannot be changed once a room is made (the gateway has no route for it). Delete asks
 * first and lives with the page, because the list's menu deletes too.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { api } from '../../api/client'
import type { MemberRosterRow } from '../../api/client/agents'
import { errorCode } from '../../api/cards'
import { MAX_ROOM_NAME_CHARS, roomQueryKey, roomsQueryKey, type RoomSummary } from '../../api/rooms'
import CrewAvatar from '../../components/CrewAvatar'
import ErrorNotice from '../../components/ErrorNotice'
import { Btn, Input } from '../../components/ui'
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog'
import { i18nT } from '../../i18n/t'
import { errText } from './roomLogic'

export default function RoomDetailsDialog({ room, byName, roomsOff, onClose, onDelete, onRoomsOff }: {
  /** The room shown; `null` keeps the dialog closed. */
  room: RoomSummary | null
  byName: ReadonlyMap<string, MemberRosterRow>
  roomsOff: boolean
  onClose: () => void
  onDelete: (room: RoomSummary) => void
  onRoomsOff: () => void
}) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [problem, setProblem] = useState('')
  useEffect(() => {
    setName(room?.name ?? '')
    setProblem('')
  }, [room?.id, room?.name])

  const trimmed = name.trim().replace(/\s+/g, ' ')
  const changed = room !== null && trimmed !== room.name
  const valid = trimmed.length > 0 && trimmed.length <= MAX_ROOM_NAME_CHARS

  const rename = useMutation({
    mutationFn: () => api.rooms.rename(room?.id ?? '', trimmed),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: roomsQueryKey() })
      if (room) void queryClient.invalidateQueries({ queryKey: roomQueryKey(room.id) })
      onClose()
    },
    onError: (e) => {
      if (errorCode(e) === 'group_rooms_off') onRoomsOff()
      setProblem(errText(e))
    },
  })

  return (
    <Dialog open={room !== null} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent maxWidth={440} data-testid="room-details">
        <DialogHeader>
          <DialogTitle>{i18nT('pages.roomsPage.details_title')}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <form
            id="room-details-form"
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              if (changed && valid && !roomsOff && !rename.isPending) rename.mutate()
            }}
          >
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-text-strong">{i18nT('pages.roomsPage.name_label')}</span>
              <Input
                value={name}
                maxLength={MAX_ROOM_NAME_CHARS + 20}
                disabled={roomsOff}
                onChange={(e) => setName(e.target.value)}
                autoFocus
                data-testid="room-rename-input"
              />
            </label>
            {trimmed.length > MAX_ROOM_NAME_CHARS && (
              <p className="m-0 text-[13px] text-danger-text" role="status">
                {i18nT('pages.roomsPage.name_too_long', { max: MAX_ROOM_NAME_CHARS })}
              </p>
            )}
            <div className="flex flex-col gap-2">
              <span className="text-sm font-medium text-text-strong">{i18nT('pages.roomsPage.members_label')}</span>
              <ul className="m-0 flex list-none flex-col p-0" data-testid="room-details-members">
                {(room?.members ?? []).map((member) => (
                  <li key={member} className="flex min-h-11 items-center gap-3 text-sm text-text">
                    <CrewAvatar seed={member} avatar={byName.get(member)?.avatar} size={24} className="shrink-0" />
                    <span className="min-w-0 truncate">{byName.get(member)?.display_name || member}</span>
                  </li>
                ))}
              </ul>
            </div>
            <ErrorNotice message={problem} onDismiss={() => setProblem('')} testId="room-rename-error" />
          </form>
        </DialogBody>
        <DialogFooter>
          <Btn danger onClick={() => room && onDelete(room)} data-testid="room-delete" className="mr-auto">
            <Trash2 size={14} aria-hidden="true" /> {i18nT('pages.roomsPage.delete')}
          </Btn>
          <Btn onClick={onClose} data-testid="room-details-cancel">{i18nT('pages.roomsPage.cancel')}</Btn>
          <Btn
            primary
            type="submit"
            form="room-details-form"
            disabled={!changed || !valid || roomsOff || rename.isPending}
            data-testid="room-rename-save"
          >
            {i18nT('pages.roomsPage.save')}
          </Btn>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
