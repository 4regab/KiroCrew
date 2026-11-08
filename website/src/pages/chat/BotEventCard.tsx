import { memo, useId } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Ban, ChevronRight, MessagesSquare } from 'lucide-react'

import { membersRosterQuery } from '../../api/membersQuery'
import CrewAvatar from '../../components/CrewAvatar'
import { i18nT } from '../../i18n/t'
import { useLanguageGeneration } from '../../i18n/useLanguageGeneration'
import type { ChatMessage } from '../../types'
import { type BotEvent, firstLine, readBotEvent } from './botEvent'
import { MarkdownDisclosureBody } from './MarkdownDisclosureCard'
import NoticeCard from './NoticeCard'
import { useRowDisclosure } from './rowDisclosure'

/** The localized reason for a refusal code the gateway gave. A code this build does not know reads as the generic one. */
function reasonText(code: string): string {
  switch (code) {
    case 'rate_limited':
      return i18nT('pages.chat.botEventCard.reason_rate_limited')
    case 'hop_limit':
    case 'round_limit':
      return i18nT('pages.chat.botEventCard.reason_chain_limit')
    case 'turn_cap':
      return i18nT('pages.chat.botEventCard.reason_turn_cap')
    case 'unknown_bot':
    case 'unknown_room':
      return i18nT('pages.chat.botEventCard.reason_no_match')
    case 'self_send':
      return i18nT('pages.chat.botEventCard.reason_self')
    case 'target_unavailable':
    case 'thread_unavailable':
      return i18nT('pages.chat.botEventCard.reason_unavailable')
    case 'message_too_long':
      return i18nT('pages.chat.botEventCard.reason_too_long')
    default:
      return i18nT('pages.chat.botEventCard.reason_other')
  }
}

function titleOf(event: BotEvent): string {
  switch (event.type) {
    case 'sent':
      return i18nT('pages.chat.botEventCard.sent', { me: event.me, peer: event.peer })
    case 'received':
      return i18nT('pages.chat.botEventCard.received', { peer: event.peer })
    case 'room_post':
      return i18nT('pages.chat.botEventCard.room_post', { me: event.me, room: event.room })
    case 'room_wake':
      return i18nT('pages.chat.botEventCard.room_wake', { room: event.room, n: event.messages.length + event.omitted })
    case 'refused':
      return event.peer
        ? i18nT('pages.chat.botEventCard.refused_bot', { peer: event.peer, reason: reasonText(event.reason) })
        : i18nT('pages.chat.botEventCard.refused_room', { room: event.room, reason: reasonText(event.reason) })
  }
}

/** The one line shown beside the title when collapsed. */
function glimpseOf(event: BotEvent): string {
  if (event.type === 'room_wake') {
    const last = event.messages[event.messages.length - 1]
    return last ? firstLine(last.text) : ''
  }
  return event.type === 'refused' ? '' : firstLine(event.text)
}

/**
 * A Bot event row in the Bot's own chat: "Ace sent a message to Four", "Message from Four",
 * "Ace posted in Office", "Group chat: Office (3 new)", or a muted line saying why a send was
 * refused. One quiet line collapsed; a button with `aria-expanded` opens the text, which is
 * data from a peer and is shown as plain text. A refusal has nothing to open.
 *
 * Shares the grammar of the other transcript notices (ring, `bg-card`, 13px type) so the rows
 * read as one family. Expansion is per-row disclosure state, kept across the transcript's
 * virtualised remounts like the compaction card's.
 */
const BotEventCard = memo(function BotEventCard({ message, disclosureKey }: { message: ChatMessage; disclosureKey?: string }) {
  // memo() boundary rendering i18nT() strings: subscribe so a language switch repaints.
  useLanguageGeneration()
  const [expanded, setExpanded] = useRowDisclosure(disclosureKey, false)
  const roster = useQuery(membersRosterQuery).data
  const headlineId = useId()
  const bodyId = useId()
  const event = readBotEvent(message)
  if (!event) return <NoticeCard content={message.content} />

  const avatar = (name: string, size = 18) => (
    <CrewAvatar seed={name} avatar={roster?.find((r) => r.name === name)?.avatar} size={size} className="shrink-0" />
  )
  const title = titleOf(event)
  const glimpse = glimpseOf(event)

  const lead =
    event.type === 'sent' ? (
      <>
        {avatar(event.me)}
        <ArrowRight size={12} className="lucide-inline shrink-0" aria-hidden="true" />
        {avatar(event.peer)}
      </>
    ) : event.type === 'received' ? (
      <>
        {avatar(event.peer)}
        <ArrowRight size={12} className="lucide-inline shrink-0" aria-hidden="true" />
        {avatar(event.me)}
      </>
    ) : event.type === 'refused' ? (
      <Ban size={13} className="lucide-inline shrink-0" aria-hidden="true" />
    ) : (
      <MessagesSquare size={13} className="lucide-inline shrink-0" aria-hidden="true" />
    )

  const header = (
    <>
      {event.type !== 'refused' && (
        <ChevronRight
          size={13}
          className={`lucide-inline shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`}
          aria-hidden="true"
        />
      )}
      {lead}
      <span
        id={headlineId}
        className={`min-w-0 font-medium text-text ${event.type === 'refused' ? 'break-words' : 'shrink-0 max-w-[60%] truncate'}`}
      >
        {title}
      </span>
      {glimpse && !expanded && (
        <span className="min-w-0 truncate text-[12px] leading-5 opacity-75">{glimpse}</span>
      )}
    </>
  )

  const body =
    event.type === 'room_wake' ? (
      <ul className="m-0 flex list-none flex-col gap-2 p-0" data-testid="bot-event-messages">
        {event.messages.map((m, i) => (
          <li key={i} className="min-w-0">
            <span className="font-medium text-text">{m.from === '' ? i18nT('pages.roomsPage.you') : m.from}</span>
            <p className="m-0 whitespace-pre-wrap break-words">{m.text}</p>
          </li>
        ))}
        {event.omitted > 0 && (
          <li className="opacity-75">{i18nT('pages.chat.botEventCard.older_not_shown', { n: event.omitted })}</li>
        )}
      </ul>
    ) : event.type === 'refused' ? null : (
      <div className="min-w-0">
        {event.type === 'sent' && event.queued && (
          <p className="m-0 mb-1 opacity-75">{i18nT('pages.chat.botEventCard.queued_note', { peer: event.peer })}</p>
        )}
        <p className="m-0 whitespace-pre-wrap break-words">{event.text}</p>
      </div>
    )

  return (
    <div
      className="self-center w-full max-w-full min-w-0 rounded-md ring-1 ring-inset forced-colors:border ring-border bg-card text-muted animate-scale-in"
      data-testid="bot-event-card"
      data-event={event.type}
      data-expanded={body ? expanded : undefined}
    >
      {body ? (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          aria-controls={bodyId}
          className="w-full flex items-center gap-2 px-3 py-2 min-w-0 text-left text-[13px] leading-5 font-body text-inherit bg-transparent border-none hover:bg-bg-hover hover:text-text transition-colors cursor-pointer"
          data-testid="bot-event-toggle"
        >
          {header}
        </button>
      ) : (
        <div className="flex items-center gap-2 px-3 py-2 min-w-0 text-[13px] leading-5" data-testid="bot-event-refused">
          {header}
        </div>
      )}
      {body && expanded && (
        <MarkdownDisclosureBody id={bodyId} labelledBy={headlineId} testId="bot-event-body">
          {body}
        </MarkdownDisclosureBody>
      )}
    </div>
  )
})

export default BotEventCard
