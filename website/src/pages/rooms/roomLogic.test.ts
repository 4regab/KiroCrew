import { describe, expect, it } from 'vitest'
import type { RoomMessage } from '../../api/rooms'
import {
  applyMention,
  createBlock,
  EVERYONE,
  isWrappingUp,
  mergeMessages,
  messageRuns,
  splitMentions,
  withMentions,
  mentionCandidates,
  mentionQuery,
  toggleMember,
} from './roomLogic'

const bots = (n: number) => Array.from({ length: n }, (_, i) => `bot${i + 1}`)
const msg = (depth: number): RoomMessage => ({ seq: 1, from: 'atlas', text: 'x', depth, ts: 0 })

describe('createBlock: two to six Bots', () => {
  it('blocks one Bot and seven Bots, and lets two through six', () => {
    expect(createBlock(bots(1))).toBe('few')
    expect(createBlock(bots(0))).toBe('few')
    expect(createBlock(bots(7))).toBe('many')
    for (const n of [2, 3, 4, 5, 6]) expect(createBlock(bots(n)), String(n)).toBeNull()
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

describe('messageRuns: what the transcript draws around a message', () => {
  const at = (seq: number, from: string, ts = seq * 10): RoomMessage => ({ seq, from, text: String(seq), depth: 0, ts })

  it('names a Bot once for a run of its messages, and puts its face on the last of the run', () => {
    const runs = messageRuns([at(1, ''), at(2, 'atlas'), at(3, 'atlas'), at(4, 'bolt'), at(5, 'atlas')])
    expect(runs.map((r) => r.showName)).toEqual([false, true, false, true, true])
    expect(runs.map((r) => r.showFace)).toEqual([false, false, true, true, true])
  })

  it('shows a time label first and after a pause of more than thirty minutes, and starts a new name run there', () => {
    const runs = messageRuns([at(1, 'atlas', 0), at(2, 'atlas', 60), at(3, 'atlas', 60 + 31 * 60)])
    expect(runs.map((r) => r.showTime)).toEqual([true, false, true])
    expect(runs.map((r) => r.showName)).toEqual([true, false, true])
    expect(runs.map((r) => r.showFace)).toEqual([false, true, true])
  })
})

describe('splitMentions and withMentions', () => {
  it('cuts the room\'s own @names out of the text, longest name first, and leaves addresses alone', () => {
    const members = ['Pipo', 'Pipo · SWE', 'Atlas']
    expect(splitMentions('hi @Pipo · SWE and @atlas, mail me@Atlas.com', members)).toEqual([
      { mention: false, text: 'hi ' },
      { mention: true, text: '@Pipo · SWE' },
      { mention: false, text: ' and ' },
      { mention: true, text: '@atlas' },
      { mention: false, text: ', mail me@Atlas.com' },
    ])
    expect(splitMentions('@everyone look', members)[0]).toEqual({ mention: true, text: '@everyone' })
    expect(splitMentions('@Nobody', members)).toEqual([{ mention: false, text: '@Nobody' }])
  })

  it('puts the picked names in front of the typed text', () => {
    expect(withMentions(['Atlas', 'everyone'], 'go')).toBe('@Atlas @everyone go')
    expect(withMentions([], 'go')).toBe('go')
  })
})
