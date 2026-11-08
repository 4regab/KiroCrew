import { describe, expect, it } from 'vitest'
import type { RoomMessage } from '../../api/rooms'
import {
  applyMention,
  createBlock,
  EVERYONE,
  isWrappingUp,
  mergeMessages,
  mentionCandidates,
  mentionQuery,
  toggleMember,
} from './roomLogic'

const bots = (n: number) => Array.from({ length: n }, (_, i) => `bot${i + 1}`)
const msg = (depth: number): RoomMessage => ({ seq: 1, from: 'atlas', text: 'x', depth, ts: 0 })

describe('createBlock: two to six Bots and a name', () => {
  it('blocks one Bot and seven Bots, and lets two through six', () => {
    expect(createBlock('Lab', bots(1))).toBe('few')
    expect(createBlock('Lab', bots(0))).toBe('few')
    expect(createBlock('Lab', bots(7))).toBe('many')
    for (const n of [2, 3, 4, 5, 6]) expect(createBlock('Lab', bots(n)), String(n)).toBeNull()
  })

  it('needs a name that is not blank and not over 60 characters', () => {
    expect(createBlock('   ', bots(2))).toBe('name')
    expect(createBlock('x'.repeat(61), bots(2))).toBe('name')
    expect(createBlock('x'.repeat(60), bots(2))).toBeNull()
  })
})

describe('toggleMember', () => {
  it('adds, removes, and never grows past six', () => {
    expect(toggleMember(['a'], 'b')).toEqual(['a', 'b'])
    expect(toggleMember(['a', 'b'], 'a')).toEqual(['b'])
    const full = bots(6)
    expect(toggleMember(full, 'bot7')).toEqual(full)
    // A picked Bot can still be removed from a full room.
    expect(toggleMember(full, 'bot3')).toHaveLength(5)
  })
})

describe('the @ list', () => {
  it('finds the @ the caret is in, at the start or after a space', () => {
    expect(mentionQuery('@at', 3)).toEqual({ start: 0, query: 'at' })
    expect(mentionQuery('hello @bo', 9)).toEqual({ start: 6, query: 'bo' })
    expect(mentionQuery('hello @', 7)).toEqual({ start: 6, query: '' })
  })

  it('ignores an address, a caret before the @, a new line and a long run of text', () => {
    expect(mentionQuery('mail me@bolt.com', 16)).toBeNull()
    expect(mentionQuery('hello @bo', 5)).toBeNull()
    expect(mentionQuery('@a\nb', 4)).toBeNull()
    expect(mentionQuery('@' + 'x'.repeat(41), 42)).toBeNull()
    expect(mentionQuery('no mention', 10)).toBeNull()
  })

  it('offers the room members whose names begin with the text, then everyone', () => {
    const members = ['Atlas', 'Bolt', 'Cleo']
    expect(mentionCandidates(members, '')).toEqual(['Atlas', 'Bolt', 'Cleo', EVERYONE])
    expect(mentionCandidates(members, 'b')).toEqual(['Bolt'])
    expect(mentionCandidates(members, 'EV')).toEqual([EVERYONE])
    expect(mentionCandidates(members, 'zz')).toEqual([])
  })

  it('handles a Bot name with a space', () => {
    expect(mentionQuery('hi @Code Rev', 12)).toEqual({ start: 3, query: 'Code Rev' })
    expect(mentionCandidates(['Code Reviewer'], 'Code Rev')).toEqual(['Code Reviewer'])
  })

  it('writes the chosen name and puts the caret after it', () => {
    expect(applyMention('hi @bo and more', 6, 3, 'Bolt')).toEqual({ text: 'hi @Bolt  and more', caret: 9 })
    expect(applyMention('@', 1, 0, EVERYONE)).toEqual({ text: '@everyone ', caret: 10 })
  })
})

describe('isWrappingUp', () => {
  it('turns on from the newest message at depth 3, and not for an empty or shallow log', () => {
    expect(isWrappingUp([])).toBe(false)
    expect(isWrappingUp([msg(0), msg(2)])).toBe(false)
    expect(isWrappingUp([msg(0), msg(3)])).toBe(true)
    expect(isWrappingUp([msg(4)])).toBe(true)
    // A new user message (depth 0) after a long exchange starts a fresh round.
    expect(isWrappingUp([msg(3), msg(0)])).toBe(false)
  })
})

describe('mergeMessages', () => {
  it('joins pages oldest first and keeps one copy of a repeated message', () => {
    const at = (seq: number): RoomMessage => ({ seq, from: '', text: String(seq), depth: 0, ts: 0 })
    expect(mergeMessages([at(3), at(4)], [at(1), at(2), at(3)]).map((m) => m.seq)).toEqual([1, 2, 3, 4])
    expect(mergeMessages([], [])).toEqual([])
  })
})
