/**
 * The facts of a Bot event row (`kind: "bot_event"`): what a Bot sent, received or was
 * refused. The gateway writes them (`dashboard/bot_events.py`) as `meta.bot_event` on an
 * assistant-role system notice. The text in them is data from a peer: it is read as plain
 * text and never as markdown.
 *
 * The row is read from a transcript line, so each field is checked here and a row that does
 * not validate is drawn from its English `content` instead of guessing.
 */
import type { ChatMessage } from '../../types'

export type BotEvent =
  | { type: 'sent'; me: string; peer: string; text: string; queued: boolean }
  | { type: 'received'; me: string; peer: string; text: string; hop: number }
  | { type: 'room_post'; me: string; room: string; text: string }
  | { type: 'room_wake'; me: string; room: string; messages: { from: string; text: string }[]; omitted: number }
  | { type: 'refused'; me: string; peer: string; room: string; reason: string }

/** Most messages a room-wake row lists, as the gateway writes it. */
const MAX_LISTED = 20

const str = (v: unknown): string => (typeof v === 'string' ? v : '')

/** The event on a row, or `null` when the row carries none that validates. */
export function readBotEvent(message: Pick<ChatMessage, 'meta'>): BotEvent | null {
  const raw = message.meta?.bot_event as Record<string, unknown> | undefined
  if (!raw || typeof raw !== 'object') return null
  const me = str(raw.me)
  switch (raw.type) {
    case 'sent': {
      const peer = str(raw.peer)
      return peer ? { type: 'sent', me, peer, text: str(raw.text), queued: raw.queued === true } : null
    }
    case 'received': {
      const peer = str(raw.peer)
      return peer ? { type: 'received', me, peer, text: str(raw.text), hop: Number(raw.hop) || 0 } : null
    }
    case 'room_post': {
      const room = str(raw.room)
      return room ? { type: 'room_post', me, room, text: str(raw.text) } : null
    }
    case 'room_wake': {
      const room = str(raw.room)
      if (!room || !Array.isArray(raw.messages)) return null
      const messages = raw.messages
        .slice(0, MAX_LISTED)
        .map((m: unknown) => ({ from: str((m as { from?: unknown })?.from), text: str((m as { text?: unknown })?.text) }))
      return { type: 'room_wake', me, room, messages, omitted: Math.max(0, Number(raw.omitted) || 0) }
    }
    case 'refused': {
      const peer = str(raw.peer)
      const room = str(raw.room)
      return peer || room ? { type: 'refused', me, peer, room, reason: str(raw.reason) } : null
    }
    default:
      return null
  }
}

/** The first line of *text*, for the collapsed row. */
export function firstLine(text: string): string {
  return text.split('\n', 1)[0].trim()
}
