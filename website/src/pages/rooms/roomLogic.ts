/**
 * The rules of the room screen that need no React: who may be picked, what the
 * `@` list offers, and when the "wrapping up" line shows. Kept apart so the
 * limits are tested without drawing anything.
 */
import {
  MAX_ROOM_MEMBERS,
  MAX_ROOM_NAME_CHARS,
  MIN_ROOM_MEMBERS,
  ROOM_WRAP_DEPTH,
  type RoomMessage,
} from '../../api/rooms'

/** The name `@everyone` pulls the whole room in with. */
export const EVERYONE = 'everyone'

/** Why the create form is not ready, or `null` when it is. */
export type CreateBlock = 'name' | 'few' | 'many' | null

export function createBlock(name: string, members: readonly string[]): CreateBlock {
  const trimmed = name.trim()
  if (!trimmed || trimmed.length > MAX_ROOM_NAME_CHARS) return 'name'
  if (members.length < MIN_ROOM_MEMBERS) return 'few'
  if (members.length > MAX_ROOM_MEMBERS) return 'many'
  return null
}

/** The selection after a click: removes a picked Bot, adds an unpicked one unless the room is full. */
export function toggleMember(selected: readonly string[], name: string): string[] {
  if (selected.includes(name)) return selected.filter((n) => n !== name)
  if (selected.length >= MAX_ROOM_MEMBERS) return [...selected]
  return [...selected, name]
}

/** The `@` the caret is inside: where it starts and what has been typed after it. */
export interface MentionQuery {
  start: number
  query: string
}

/** Longest `@` query worth matching; past it the person is writing, not picking a name. */
const MAX_QUERY_CHARS = 40

/**
 * The `@word` ending at *caret*, if any. The `@` must open the text or follow
 * whitespace (an address like `me@bolt.com` is not a mention), and the word may
 * hold spaces because a Bot's name can.
 */
export function mentionQuery(text: string, caret: number): MentionQuery | null {
  const before = text.slice(0, caret)
  const start = before.lastIndexOf('@')
  if (start < 0) return null
  if (start > 0 && !/\s/.test(before[start - 1])) return null
  const query = before.slice(start + 1)
  if (query.length > MAX_QUERY_CHARS || query.includes('\n')) return null
  return { start, query }
}

/**
 * What the `@` list offers: the room's Bots, then `@everyone`, whose names begin
 * with what was typed (ignoring case). The room's own members only: the
 * gateway wakes no one else.
 */
export function mentionCandidates(members: readonly string[], query: string): string[] {
  const q = query.toLowerCase()
  return [...members, EVERYONE].filter((n) => n.toLowerCase().startsWith(q))
}

/** The text and caret after choosing *name* for the `@` at *start*. */
export function applyMention(text: string, caret: number, start: number, name: string): { text: string; caret: number } {
  const insert = `@${name} `
  return { text: text.slice(0, start) + insert + text.slice(caret), caret: start + insert.length }
}

/**
 * Whether the room is nearly out of rounds. The gateway tells the Bots to post only
 * if essential from this depth and wakes nobody past the next one, so the newest
 * message's depth is the whole signal.
 */
export function isWrappingUp(messages: readonly RoomMessage[]): boolean {
  const last = messages[messages.length - 1]
  return Boolean(last) && last.depth >= ROOM_WRAP_DEPTH
}

/** The text of a failed call. An `ApiError` already carries the gateway's own words. */
export function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
