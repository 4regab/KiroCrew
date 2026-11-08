/**
 * One message of a room, drawn like a chat: the user's on the right in a grey bubble,
 * a Bot's on the left with its name above in the colour of its avatar and its face at
 * the bottom-left. `from` is `""` for the user and a Bot's configured name otherwise,
 * set by the gateway, and the text is plain: it renders as text with its line breaks
 * kept, never as markup. An `@name` in the text is drawn as a chip. A message that
 * answers another shows what it answers above its text.
 */
import { Reply } from 'lucide-react'
import type { MemberRosterRow } from '../../api/client/agents'
import type { RoomMessage as RoomMessageRow } from '../../api/rooms'
import CrewAvatar from '../../components/CrewAvatar'
import { IconButton } from '../../components/ui'
import { i18nT } from '../../i18n/t'
import { botTile, EDGE_TONE, NAME_TONE, splitMentions, tileVar } from './roomLogic'

export default function RoomMessage({ message, members, byName, showName, showFace, onReply }: {
  message: RoomMessageRow
  members: readonly string[]
  byName: ReadonlyMap<string, MemberRosterRow>
  showName: boolean
  showFace: boolean
  /** Not given while the room is read-only. */
  onReply?: (message: RoomMessageRow) => void
}) {
  const mine = message.from === ''
  const row = byName.get(message.from)
  const label = row?.display_name || message.from
  const tile = botTile(message.from, row?.avatar)
  const quote = message.quote
  const quoteRow = quote ? byName.get(quote.from) : undefined
  const quoteName = quote ? (quote.from === '' ? i18nT('pages.roomsPage.you') : quoteRow?.display_name || quote.from) : ''

  return (
    <li
      className={`group flex items-end gap-2 ${mine ? 'flex-row-reverse' : ''}`}
      data-testid={mine ? 'room-message-user' : 'room-message-bot'}
    >
      {!mine && (
        <span className="mb-0.5 inline-flex size-7 shrink-0 items-end">
          {showFace && <CrewAvatar seed={message.from} avatar={row?.avatar} size={28} />}
        </span>
      )}
      <div className={`flex min-w-0 max-w-[85%] flex-col gap-0.5 md:max-w-[75%] ${mine ? 'items-end' : 'items-start'}`}>
        {!mine && showName && (
          <span className={`px-3 text-[13px] font-medium ${NAME_TONE}`} style={tileVar(tile)} data-testid="room-message-name">
            {label}
          </span>
        )}
        <div className={`flex items-center gap-1 ${mine ? 'flex-row-reverse' : ''}`}>
          <div
            className={`min-w-0 rounded-2xl px-3.5 py-2 text-sm text-text ${
              mine ? 'bg-bg-hover text-text-strong' : 'border border-border bg-bg-elevated'
            }`}
          >
            {quote && (
              <blockquote
                className={`m-0 mb-1.5 border-l-2 pl-2 text-[13px] text-muted ${EDGE_TONE}`}
                style={tileVar(botTile(quote.from, quoteRow?.avatar))}
                data-testid="room-message-quote"
              >
                <span className="block font-medium">{quoteName}</span>
                <span className="line-clamp-2 break-words">{quote.text}</span>
              </blockquote>
            )}
            <p className="m-0 whitespace-pre-wrap break-words">
              {splitMentions(message.text, members).map((part, i) =>
                part.mention ? (
                  <span key={i} className="rounded-md bg-accent-subtle px-1 font-medium text-text-strong" data-testid="room-mention-chip">
                    {part.text}
                  </span>
                ) : (
                  part.text
                ),
              )}
            </p>
          </div>
          {onReply && (
            <IconButton
              aria-label={i18nT('pages.roomsPage.reply')}
              onClick={() => onReply(message)}
              className="size-9 shrink-0 text-muted md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100"
              data-testid="room-reply"
            >
              <Reply size={14} aria-hidden="true" />
            </IconButton>
          )}
        </div>
      </div>
    </li>
  )
}
