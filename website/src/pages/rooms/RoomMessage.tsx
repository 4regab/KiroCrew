/**
 * One line of a room's log. The sender is the gateway's own label: `from` is `""`
 * for the user and a Bot's configured name otherwise, and a message cannot
 * choose it. The text is plain: rooms carry text only, so it renders as text with
 * its line breaks kept, never as markup.
 */
import type { MemberRosterRow } from '../../api/client/agents'
import type { RoomMessage as RoomMessageRow } from '../../api/rooms'
import CrewAvatar from '../../components/CrewAvatar'
import { fmtTime } from '../../i18n/format'
import { i18nT } from '../../i18n/t'

export default function RoomMessage({ message, row }: {
  message: RoomMessageRow
  /** The sender's roster row, for the face and display label; absent for the user or a removed Bot. */
  row?: MemberRosterRow
}) {
  const mine = message.from === ''
  const sender = mine ? i18nT('pages.roomsPage.you') : row?.display_name || message.from
  return (
    <li
      className={`flex gap-2 ${mine ? 'flex-row-reverse' : ''}`}
      data-testid={mine ? 'room-message-user' : 'room-message-bot'}
    >
      {!mine && <CrewAvatar seed={message.from} avatar={row?.avatar} size={28} className="mt-0.5 shrink-0" />}
      <div className={`flex min-w-0 max-w-[85%] flex-col gap-0.5 ${mine ? 'items-end' : 'items-start'}`}>
        <div className="flex items-baseline gap-2 text-[13px] text-muted">
          <span className="font-medium text-text">{sender}</span>
          <time dateTime={new Date(message.ts * 1000).toISOString()}>{fmtTime(message.ts * 1000)}</time>
        </div>
        <p
          className={`m-0 whitespace-pre-wrap break-words rounded-lg border px-3 py-2 text-sm ${
            mine ? 'border-accent bg-accent-subtle text-text-strong' : 'border-border bg-bg-elevated text-text'
          }`}
        >
          {message.text}
        </p>
      </div>
    </li>
  )
}
