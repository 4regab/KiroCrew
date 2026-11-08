/**
 * Types and limits for group chat rooms (`/api/rooms`, served by
 * `kiro_crew/dashboard/handlers/rooms.py`).
 *
 * A room is the user plus two to six Bots. The gateway owns it: the dashboard
 * reads its log and posts the user's own messages, and each Bot speaks only
 * through its own `room_send` tool. The numbers below mirror
 * `kiro_crew/dashboard/group_rooms.py`; the gateway enforces them and says so
 * (`too_few_members`, `message_too_long`, ...), so this file only lets the form
 * stop a request the gateway would refuse.
 */

export interface RoomSummary {
  id: string
  name: string
  /** Configured Bot names, the identity every other Bot route is keyed on. */
  members: string[]
  created: number
  /** The newest message's sequence number; 0 for an empty room. */
  seq: number
}

export interface RoomMessage {
  seq: number
  /** `""` is the user. Anything else is a Bot's configured name, set by the gateway. */
  from: string
  text: string
  /** 0 for the user; a reply is one deeper than the message that woke it. */
  depth: number
  ts: number
}

export interface RoomDetail extends RoomSummary {
  /** The newest page of the log, oldest first. */
  messages: RoomMessage[]
  /** Whether older messages than the first one here remain on the gateway. */
  has_more: boolean
}

export const MIN_ROOM_MEMBERS = 2
export const MAX_ROOM_MEMBERS = 6
export const MAX_ROOM_NAME_CHARS = 60
/** From this depth the gateway tells the Bots the room is wrapping up. */
export const ROOM_WRAP_DEPTH = 3

/**
 * ponytail: the open room and the list are re-read on a timer; the gateway has no
 * push channel for rooms. Every tick of the open room carries its newest 200
 * messages, so this is fine for a handful of rooms. Upgrade path: a
 * `since=<seq>` parameter on `GET /api/rooms/{id}`, then a WebSocket event.
 */
export const ROOM_POLL_MS = 5_000
export const ROOM_LIST_POLL_MS = 15_000

export const roomsQueryKey = () => ['rooms'] as const
export const roomQueryKey = (id: string) => ['rooms', id] as const
