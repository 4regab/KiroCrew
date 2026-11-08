/**
 * The rules of the room screen that need no React: who may be picked, what the
 * `@` list offers, and when the "wrapping up" line shows. Kept apart so the
 * limits are tested without drawing anything.
 */
import type { CSSProperties } from 'react'
import { ghostTraitsFrom, iconAvatarFrom, seededTraits } from '../../components/CrewAvatar'
import {
  MAX_ROOM_MEMBERS,
  MIN_ROOM_MEMBERS,
  ROOM_WRAP_DEPTH,
  type RoomMessage,
} from '../../api/rooms'

/** The name `@everyone` pulls the whole room in with. */
export const EVERYONE = 'everyone'

/** Why a group cannot be started yet, or `null` when it can. */
export type CreateBlock = 'few' | 'many' | null

export function createBlock(members: readonly string[]): CreateBlock {
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

/** Two runs of the log as one: unique by `seq`, oldest first. */
export function mergeMessages(a: readonly RoomMessage[], b: readonly RoomMessage[]): RoomMessage[] {
  const bySeq = new Map<number, RoomMessage>()
  for (const m of a) bySeq.set(m.seq, m)
  for (const m of b) bySeq.set(m.seq, m)
  return [...bySeq.values()].sort((x, y) => x.seq - y.seq)
}

/**
 * The colour a Bot's name wears: the tile behind its avatar (the pose background of an
 * icon avatar, the tile of a pinned or name-derived ghost), so the name and the face
 * match. A picture or pack avatar has no tile and falls back to the name-derived one.
 */
export function botTile(name: string, avatar: unknown): string {
  return iconAvatarFrom(avatar)?.bg ?? ghostTraitsFrom(avatar)?.tile ?? seededTraits(name).tile
}

/**
 * The tile as a CSS variable on an element. Its classes (`NAME_TONE`, `EDGE_TONE`) pull the
 * tile toward the text colour, so a dark tile stays readable on a dark page and a light one
 * on a light page.
 */
export function tileVar(tile: string): CSSProperties {
  return { ['--bot-tile' as string]: tile }
}

/** A Bot's name in the colour of its avatar tile. */
export const NAME_TONE = 'text-[color-mix(in_srgb,var(--bot-tile)_70%,var(--text-strong))]'
/** The bar beside a quoted message, in the colour of the quoted Bot's tile. */
export const EDGE_TONE = 'border-[color-mix(in_srgb,var(--bot-tile)_70%,var(--text-strong))]'

/** A gap in the log longer than this starts a new time label. */
export const TIME_GAP_S = 30 * 60

export interface MessageRun {
  message: RoomMessage
  /** A time label goes above: the first message, or after a long pause. */
  showTime: boolean
  /** The Bot's name goes above: the first message of a run from the same Bot. */
  showName: boolean
  /** The Bot's face goes beside: the last message of a run. */
  showFace: boolean
}

/** Each message with what the transcript draws around it. Messages from the same Bot in a row share one name. */
export function messageRuns(messages: readonly RoomMessage[]): MessageRun[] {
  return messages.map((message, i) => {
    const prev = messages[i - 1]
    const next = messages[i + 1]
    const bot = message.from !== ''
    const sameAsPrev = Boolean(prev) && prev.from === message.from
    return {
      message,
      showTime: !prev || message.ts - prev.ts > TIME_GAP_S,
      showName: bot && !(sameAsPrev && message.ts - prev.ts <= TIME_GAP_S),
      showFace: bot && !(next && next.from === message.from && next.ts - message.ts <= TIME_GAP_S),
    }
  })
}

export type TextPart = { mention: boolean; text: string }

/**
 * *text* cut into plain runs and `@name` / `@everyone` mentions, so a mention can be drawn as a chip.
 * Only the room's own names count, longest first, and the `@` must open the text or follow whitespace
 * (the gateway's own rule for a cue, `group_rooms.mentions`).
 */
export function splitMentions(text: string, members: readonly string[]): TextPart[] {
  const names = [...members, EVERYONE].sort((x, y) => y.length - x.length)
  const escaped = names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const re = new RegExp(`(?<!\\S)@(?:${escaped.join('|')})(?![\\p{L}\\p{N}_])`, 'giu')
  const parts: TextPart[] = []
  let last = 0
  for (const m of text.matchAll(re)) {
    if (m.index > last) parts.push({ mention: false, text: text.slice(last, m.index) })
    parts.push({ mention: true, text: m[0] })
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push({ mention: false, text: text.slice(last) })
  return parts
}

/** The text sent for a message with picked mentions: `@A @B ` ahead of what was typed. */
export function withMentions(mentions: readonly string[], text: string): string {
  return [...mentions.map((n) => `@${n}`), text].join(' ').trim()
}
