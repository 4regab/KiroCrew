/**
 * One open room, drawn like a chat: a pill at the top (the room's face and name; it
 * opens the details), the log as bubbles, one muted line under the last message while
 * Bots are working, and the composer pill. Below `md` this pane fills the screen and a
 * back arrow sits left of the pill; from `md` up it sits beside the list and the arrow is
 * gone.
 *
 * The log is a polite live region, so a screen reader hears each new message once. It
 * follows the newest message unless the reader has scrolled up. Who is working comes from
 * the Bots' own roster rows (`running`, the same flag the left rail reads), which the page
 * re-reads on a timer.
 */
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, MessagesSquare } from 'lucide-react'
import { api } from '../../api/client'
import type { MemberRosterRow } from '../../api/client/agents'
import { isNotFoundError } from '../../api/apiError'
import { errorCode } from '../../api/cards'
import {
  ROOM_POLL_MS,
  roomQueryKey,
  type RoomDetail,
  type RoomMessage as RoomMessageRow,
} from '../../api/rooms'
import CrewAvatar from '../../components/CrewAvatar'
import ErrorNotice from '../../components/ErrorNotice'
import { Btn, ContentSkeleton, EmptyState, IconButton } from '../../components/ui'
import { fmtDateTime, fmtTime } from '../../i18n/format'
import { i18nT } from '../../i18n/t'
import RoomAvatar from './RoomAvatar'
import RoomComposer from './RoomComposer'
import RoomMessage from './RoomMessage'
import { errText, isWrappingUp, mergeMessages, messageRuns } from './roomLogic'

/** Within this many px of the bottom counts as "reading the newest message". */
const STICK_PX = 80

/** "Klaus, Heim and 3 more are working…": the first two names, then a count. */
function workingText(names: readonly string[]): string {
  const [a, b, c] = names
  if (names.length === 0) return ''
  if (names.length === 1) return i18nT('pages.roomsPage.working_1', { a })
  if (names.length === 2) return i18nT('pages.roomsPage.working_2', { a, b })
  if (names.length === 3) return i18nT('pages.roomsPage.working_3', { a, b, c })
  return i18nT('pages.roomsPage.working_n', { a, b, n: names.length - 2 })
}

/** A time label: the clock for today, the date and clock for any other day. */
function timeLabel(ts: number): string {
  const when = new Date(ts * 1000)
  return when.toDateString() === new Date().toDateString() ? fmtTime(when) : fmtDateTime(when)
}

export default function RoomConversation({ roomId, roster, roomsOff, onBack, onOpenDetails, onRoomsOff }: {
  roomId: string
  roster: readonly MemberRosterRow[]
  roomsOff: boolean
  onBack: () => void
  onOpenDetails: (room: RoomDetail) => void
  onRoomsOff: () => void
}) {
  const queryClient = useQueryClient()
  const [sendError, setSendError] = useState('')
  const [replyTo, setReplyTo] = useState<RoomMessageRow | null>(null)
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
  const byName = useMemo(() => new Map(roster.map((r) => [r.name, r])), [roster])

  // Older pages the reader asked for, and whether yet older ones remain. The poll only
  // carries the newest page, so everything seen is kept and merged by `seq`.
  const [older, setOlder] = useState<{ rows: RoomMessageRow[]; more: boolean | null }>({ rows: [], more: null })
  const [olderError, setOlderError] = useState('')
  const messages = useMemo(() => mergeMessages(older.rows, room?.messages ?? []), [older.rows, room?.messages])
  const runs = useMemo(() => messageRuns(messages), [messages])
  const hasEarlier = older.more ?? room?.has_more ?? false
  const loadEarlier = useMutation({
    mutationFn: async () => (await api.rooms.get(roomId, { before: messages[0]?.seq })).room,
    onSuccess: (page) => {
      setOlderError('')
      setOlder((prev) => ({ rows: mergeMessages(prev.rows, page.messages), more: page.has_more }))
    },
    onError: (e) => setOlderError(errText(e)),
  })

  const send = useMutation({
    mutationFn: ({ text, reply }: { text: string; reply: RoomMessageRow | null }) =>
      api.rooms.post(roomId, text, reply?.seq),
    onSuccess: () => {
      setSendError('')
      setReplyTo(null)
      stick.current = true
      void queryClient.invalidateQueries({ queryKey: roomQueryKey(roomId) })
      void queryClient.invalidateQueries({ queryKey: ['rooms'] })
    },
    onError: (e) => {
      if (errorCode(e) === 'group_rooms_off') onRoomsOff()
      setSendError(errText(e))
    },
  })

  const working = (room?.members ?? [])
    .map((name) => byName.get(name))
    .filter((row): row is MemberRosterRow => Boolean(row?.running))
  const workingNames = working.map((row) => row.display_name || row.name)

  const count = messages.length
  useEffect(() => {
    const el = scroller.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [count, roomId, working.length])

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
      <header className="relative flex min-h-11 items-center justify-center">
        <IconButton
          aria-label={i18nT('pages.roomsPage.back_to_rooms')}
          onClick={onBack}
          className="absolute left-0 min-h-11 min-w-11 rounded-full md:hidden"
          data-testid="room-back"
        >
          <ArrowLeft size={18} aria-hidden="true" />
        </IconButton>
        <Btn
          disabled={!room}
          onClick={() => room && onOpenDetails(room)}
          aria-haspopup="dialog"
          aria-label={room ? i18nT('pages.roomsPage.open_details', { name: room.name }) : undefined}
          className="mx-12 min-h-11 max-w-full gap-2 rounded-full py-1 pl-2 pr-4 md:mx-0"
          data-testid="room-title-pill"
        >
          {room && <RoomAvatar members={room.members} byName={byName} size={28} />}
          <span className="truncate text-sm font-semibold text-text-strong" data-testid="room-title" role="heading" aria-level={2}>
            {room?.name ?? ''}
          </span>
        </Btn>
      </header>
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
            className="m-0 flex list-none flex-col gap-1 p-0 pb-2"
            data-testid="room-log"
          >
            {hasEarlier && (
              <li className="flex flex-col items-center gap-1 pb-2">
                <Btn onClick={() => loadEarlier.mutate()} disabled={loadEarlier.isPending} data-testid="room-load-earlier">
                  {i18nT('pages.roomsPage.load_earlier')}
                </Btn>
                {olderError && <span className="text-[13px] text-danger-text" role="alert">{olderError}</span>}
              </li>
            )}
            {runs.map(({ message, showTime, showName, showFace }) => (
              <Fragment key={message.seq}>
                {showTime && (
                  <li className="py-2 text-center text-[13px] text-muted" data-testid="room-time">
                    <time dateTime={new Date(message.ts * 1000).toISOString()}>{timeLabel(message.ts)}</time>
                  </li>
                )}
                <RoomMessage
                  message={message}
                  members={room?.members ?? []}
                  byName={byName}
                  showName={showName}
                  showFace={showFace}
                  onReply={roomsOff ? undefined : setReplyTo}
                />
              </Fragment>
            ))}
          </ol>
        )}
        {workingNames.length > 0 && (
          <p className="m-0 flex items-center gap-2 py-2 pl-1 text-[13px] text-muted" role="status" data-testid="room-working">
            <CrewAvatar seed={working[0].name} avatar={working[0].avatar} size={20} className="shrink-0" />
            <span className="min-w-0 break-words">{workingText(workingNames)}</span>
          </p>
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
          roomName={room?.name ?? ''}
          members={room?.members ?? []}
          byName={byName}
          disabled={roomsOff || !room}
          disabledReason={i18nT('pages.roomsPage.rooms_off_short')}
          sending={send.isPending}
          replyTo={replyTo}
          onCancelReply={() => setReplyTo(null)}
          onSend={async (text, reply) => {
            try {
              await send.mutateAsync({ text, reply })
              return true
            } catch {
              return false
            }
          }}
        />
      </div>
    </div>
  )
}
