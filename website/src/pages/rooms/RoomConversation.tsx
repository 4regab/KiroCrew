/**
 * One open room: header, the log, the "wrapping up" line and the composer.
 *
 * The log is a polite live region, so a screen reader hears each new message once.
 * It follows the newest message unless the reader has scrolled up. Below `md` this
 * pane fills the screen and the header carries a back arrow; from `md` up it sits
 * beside the list and the arrow is gone.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, MessagesSquare, Trash2 } from 'lucide-react'
import { api } from '../../api/client'
import type { MemberRosterRow } from '../../api/client/agents'
import { isNotFoundError } from '../../api/apiError'
import { errorCode } from '../../api/cards'
import { ROOM_POLL_MS, roomQueryKey, roomsQueryKey, type RoomDetail, type RoomMessage as RoomMessageRow } from '../../api/rooms'
import CrewAvatar from '../../components/CrewAvatar'
import ErrorNotice from '../../components/ErrorNotice'
import { useConfirm } from '../../components/ConfirmDialog'
import { Btn, ContentSkeleton, EmptyState, IconButton } from '../../components/ui'
import { i18nT } from '../../i18n/t'
import RoomComposer from './RoomComposer'
import RoomMessage from './RoomMessage'
import { errText, isWrappingUp, mergeMessages } from './roomLogic'

/** Within this many px of the bottom counts as "reading the newest message". */
const STICK_PX = 80

export default function RoomConversation({ roomId, roster, roomsOff, onBack, onDeleted, onRoomsOff }: {
  roomId: string
  roster: readonly MemberRosterRow[]
  roomsOff: boolean
  onBack: () => void
  onDeleted: () => void
  onRoomsOff: () => void
}) {
  const queryClient = useQueryClient()
  const { confirm, confirmDialog } = useConfirm()
  const [sendError, setSendError] = useState('')
  const [deleteError, setDeleteError] = useState('')
  const scroller = useRef<HTMLDivElement>(null)
  const stick = useRef(true)

  const roomQ = useQuery({
    queryKey: roomQueryKey(roomId),
    queryFn: async () => (await api.rooms.get(roomId)).room,
    refetchInterval: ROOM_POLL_MS,
    // A room that is gone answers 404 for good; do not hammer it.
    retry: (count, e) => !isNotFoundError(e) && count < 2,
  })
  const room: RoomDetail | undefined = roomQ.data
  // Older pages the reader asked for, and whether yet older ones remain. The poll only
  // carries the newest page, so everything seen is kept and merged by `seq`.
  const [older, setOlder] = useState<{ rows: RoomMessageRow[]; more: boolean | null }>({ rows: [], more: null })
  const [olderError, setOlderError] = useState('')
  const messages = useMemo(() => mergeMessages(older.rows, room?.messages ?? []), [older.rows, room?.messages])
  const hasEarlier = older.more ?? room?.has_more ?? false
  const loadEarlier = useMutation({
    mutationFn: async () => (await api.rooms.get(roomId, messages[0]?.seq)).room,
    onSuccess: (page) => {
      setOlderError('')
      setOlder((prev) => ({ rows: mergeMessages(prev.rows, page.messages), more: page.has_more }))
    },
    onError: (e) => setOlderError(errText(e)),
  })
  const byName = useMemo(() => new Map(roster.map((r) => [r.name, r])), [roster])

  const send = useMutation({
    mutationFn: (text: string) => api.rooms.post(roomId, text),
    onSuccess: () => {
      setSendError('')
      stick.current = true
      void queryClient.invalidateQueries({ queryKey: roomQueryKey(roomId) })
    },
    onError: (e) => {
      if (errorCode(e) === 'group_rooms_off') onRoomsOff()
      setSendError(errText(e))
    },
  })

  const remove = useMutation({
    mutationFn: () => api.rooms.remove(roomId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: roomsQueryKey() })
      onDeleted()
    },
    onError: (e) => setDeleteError(errText(e)),
  })

  const count = messages.length
  useEffect(() => {
    const el = scroller.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [count, roomId])

  const askDelete = async () => {
    const ok = await confirm({
      title: i18nT('pages.roomsPage.delete_title'),
      body: i18nT('pages.roomsPage.delete_body'),
      confirmLabel: i18nT('pages.roomsPage.delete'),
    })
    if (ok) remove.mutate()
  }

  if (roomQ.isError && isNotFoundError(roomQ.error)) {
    return (
      <EmptyState
        icon={<MessagesSquare size={20} />}
        title={i18nT('pages.roomsPage.room_gone')}
        action={<Btn onClick={onBack} data-testid="room-gone-back">{i18nT('pages.roomsPage.back_to_rooms')}</Btn>}
        testId="room-gone"
      />
    )
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2" data-testid="room-conversation">
      <header className="flex items-start gap-2 border-b border-border pb-2">
        <IconButton
          aria-label={i18nT('pages.roomsPage.back_to_rooms')}
          onClick={onBack}
          className="mt-0.5 min-h-11 min-w-11 md:hidden"
          data-testid="room-back"
        >
          <ArrowLeft size={18} aria-hidden="true" />
        </IconButton>
        <div className="min-w-0 flex-1">
          <h2 className="m-0 truncate text-lg font-semibold text-text-strong" data-testid="room-title">
            {room?.name ?? ''}
          </h2>
          <ul className="m-0 mt-1 flex list-none flex-wrap gap-1.5 p-0" aria-label={i18nT('pages.roomsPage.members_label')}>
            {(room?.members ?? []).map((name) => (
              <li
                key={name}
                className="flex items-center gap-1.5 rounded-full border border-border bg-bg-elevated py-0.5 pl-1 pr-2.5 text-[13px] text-text"
                data-testid="room-member-chip"
              >
                <CrewAvatar seed={name} avatar={byName.get(name)?.avatar} size={18} />
                <span className="max-w-[10rem] truncate">{byName.get(name)?.display_name || name}</span>
              </li>
            ))}
          </ul>
        </div>
        <IconButton
          aria-label={i18nT('pages.roomsPage.delete_room')}
          variant="danger"
          onClick={() => void askDelete()}
          disabled={!room || remove.isPending}
          className="mt-0.5 min-h-11 min-w-11"
          data-testid="room-delete"
        >
          <Trash2 size={16} aria-hidden="true" />
        </IconButton>
      </header>
      {deleteError && <ErrorNotice message={deleteError} onDismiss={() => setDeleteError('')} askAgent testId="room-delete-error" />}
      {roomQ.isError && (
        <ErrorNotice
          message={errText(roomQ.error)}
          askAgent
          testId="room-load-error"
          footer={<Btn onClick={() => void roomQ.refetch()} disabled={roomQ.isFetching}>{i18nT('pages.roomsPage.retry')}</Btn>}
        />
      )}
      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_PX
        }}
        className="min-h-0 flex-1 overflow-y-auto"
        data-testid="room-log-scroller"
      >
        {!room && !roomQ.isError ? (
          <ContentSkeleton rows={4} />
        ) : room && messages.length === 0 ? (
          <EmptyState
            icon={<MessagesSquare size={20} />}
            title={i18nT('pages.roomsPage.no_messages')}
            subtitle={i18nT('pages.roomsPage.no_messages_hint')}
            testId="room-empty"
          />
        ) : (
          <ol
            role="log"
            aria-live="polite"
            aria-relevant="additions"
            aria-label={i18nT('pages.roomsPage.log_label')}
            className="m-0 flex list-none flex-col gap-3 p-0 pb-2"
            data-testid="room-log"
          >
            {hasEarlier && (
              <li className="flex flex-col items-center gap-1 list-none">
                <Btn onClick={() => loadEarlier.mutate()} disabled={loadEarlier.isPending} data-testid="room-load-earlier">
                  {i18nT('pages.roomsPage.load_earlier')}
                </Btn>
                {olderError && <span className="text-[13px] text-danger-text" role="alert">{olderError}</span>}
              </li>
            )}
            {messages.map((m) => <RoomMessage key={m.seq} message={m} row={byName.get(m.from)} />)}
          </ol>
        )}
        {room && isWrappingUp(messages) && (
          <p className="m-0 py-2 text-center text-[13px] text-muted" role="note" data-testid="room-wrapping-up">
            {i18nT('pages.roomsPage.wrapping_up')}
          </p>
        )}
      </div>
      {sendError && (
        // No hand-off: the unsent draft sits right below, and a retry loses nothing.
        <ErrorNotice message={sendError} onDismiss={() => setSendError('')} testId="room-send-error" />
      )}
      <div className="pb-[env(safe-area-inset-bottom)]">
        <RoomComposer
          members={room?.members ?? []}
          disabled={roomsOff || !room}
          disabledReason={i18nT('pages.roomsPage.rooms_off_short')}
          sending={send.isPending}
          onSend={async (text) => {
            try {
              await send.mutateAsync(text)
              return true
            } catch {
              return false
            }
          }}
        />
      </div>
      {confirmDialog}
    </div>
  )
}
