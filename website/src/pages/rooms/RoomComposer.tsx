/**
 * The user's box for a room: one rounded pill. Enter sends and Shift+Enter starts a new
 * line. Typing `@` offers the room's own Bots (with their faces) and `@everyone`; the
 * list is a combobox (arrow keys move, Enter or Tab picks, Escape closes it). A picked
 * name leaves the text and becomes a chip above it, and is put back in front of the
 * message on send. The gateway wakes every Bot on every message, so a mention is a cue to
 * the Bots and not a routing rule. A reply shows what it answers above the pill. Nothing
 * is cleared until the gateway took the message, so a failed send keeps the draft.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { ArrowUp, Users, X } from 'lucide-react'
import type { MemberRosterRow } from '../../api/client/agents'
import type { RoomMessage } from '../../api/rooms'
import CrewAvatar from '../../components/CrewAvatar'
import { IconButton } from '../../components/ui'
import { useAutoGrowTextarea } from '../../hooks/useAutoGrowTextarea'
import { useImeGuard } from '../../hooks/useImeGuard'
import { i18nT } from '../../i18n/t'
import { botTile, EDGE_TONE, EVERYONE, mentionCandidates, mentionQuery, tileVar, withMentions } from './roomLogic'

export default function RoomComposer({ roomName, members, byName, disabled, disabledReason, sending, replyTo, onCancelReply, onSend }: {
  roomName: string
  members: readonly string[]
  byName: ReadonlyMap<string, MemberRosterRow>
  disabled: boolean
  /** Why typing is off, shown as the placeholder. */
  disabledReason: string
  sending: boolean
  /** The message being answered, if any. */
  replyTo: RoomMessage | null
  onCancelReply: () => void
  /** Resolves true when the gateway took the message; the draft is cleared only then. */
  onSend: (text: string, replyTo: RoomMessage | null) => Promise<boolean>
}) {
  const [value, setValue] = useState('')
  const [caret, setCaret] = useState(0)
  const [selected, setSelected] = useState(0)
  const [dismissed, setDismissed] = useState(false)
  const [mentions, setMentions] = useState<string[]>([])
  const ref = useRef<HTMLTextAreaElement>(null)
  const ime = useImeGuard()
  const listId = useId()
  useAutoGrowTextarea(ref, value, Math.round(window.innerHeight * 0.3))

  // Answering a message puts the cursor back in the box.
  useEffect(() => {
    if (replyTo) ref.current?.focus()
  }, [replyTo])

  const label = (name: string) => byName.get(name)?.display_name || name
  const query = dismissed ? null : mentionQuery(value, caret)
  const candidates = useMemo(
    () => (query ? mentionCandidates(members, query.query).filter((n) => !mentions.includes(n)) : []),
    [members, query, mentions],
  )
  const open = candidates.length > 0
  const active = Math.min(selected, Math.max(candidates.length - 1, 0))
  const canSend = !disabled && !sending && value.trim().length > 0

  const track = (el: HTMLTextAreaElement) => {
    setCaret(el.selectionStart ?? el.value.length)
    setSelected(0)
  }

  const pick = (name: string) => {
    if (!query) return
    const next = value.slice(0, query.start) + value.slice(caret)
    setValue(next)
    setCaret(query.start)
    setMentions((prev) => (prev.includes(name) ? prev : [...prev, name]))
    // Put the caret where the `@` was once React has written the new value.
    requestAnimationFrame(() => ref.current?.setSelectionRange(query.start, query.start))
    ref.current?.focus()
  }

  const send = async () => {
    if (!canSend) return
    if (await onSend(withMentions(mentions, value.trim()), replyTo)) {
      setValue('')
      setCaret(0)
      setMentions([])
    }
  }

  const replyRow = replyTo ? byName.get(replyTo.from) : undefined
  const replyName = replyTo ? (replyTo.from === '' ? i18nT('pages.roomsPage.you') : label(replyTo.from)) : ''

  return (
    <form
      className="relative flex flex-col gap-1.5"
      onSubmit={(e) => {
        e.preventDefault()
        void send()
      }}
      data-testid="room-composer"
    >
      {open && (
        <div
          id={listId}
          role="listbox"
          aria-label={i18nT('pages.roomsPage.mention_list')}
          className="absolute bottom-full left-0 z-10 mb-1 max-h-56 w-72 max-w-full overflow-y-auto rounded-xl border border-border bg-bg-elevated p-1 shadow-lg"
          data-testid="room-mention-list"
        >
          {candidates.map((name, i) => (
            <div
              key={name}
              id={`${listId}-${i}`}
              role="option"
              tabIndex={-1}
              aria-selected={i === active}
              onMouseDown={(e) => {
                // Keep focus in the text box; the click picks the name.
                e.preventDefault()
                pick(name)
              }}
              className={`flex min-h-11 cursor-pointer items-center gap-2 rounded-lg px-3 text-sm ${
                i === active ? 'bg-bg-hover text-text-strong' : 'text-text hover:bg-bg-hover'
              }`}
            >
              {name === EVERYONE ? (
                <>
                  <Users size={18} aria-hidden="true" className="shrink-0" />
                  <span className="truncate">{i18nT('pages.roomsPage.mention_everyone')}</span>
                </>
              ) : (
                <>
                  <CrewAvatar seed={name} avatar={byName.get(name)?.avatar} size={20} className="shrink-0" />
                  <span className="truncate">{label(name)}</span>
                </>
              )}
            </div>
          ))}
        </div>
      )}
      {replyTo && (
        <div
          className="flex items-start gap-2 rounded-xl border border-border bg-bg-elevated py-1.5 pl-3 pr-1"
          data-testid="room-reply-bar"
        >
          <div className={`min-w-0 flex-1 border-l-2 pl-2 text-[13px] ${EDGE_TONE}`} style={tileVar(botTile(replyTo.from, replyRow?.avatar))}>
            <span className="block truncate font-medium text-text-strong">
              {i18nT('pages.roomsPage.replying_to', { name: replyName })}
            </span>
            <span className="line-clamp-2 break-words text-muted">{replyTo.text}</span>
          </div>
          <IconButton aria-label={i18nT('pages.roomsPage.cancel_reply')} onClick={onCancelReply} className="size-9 shrink-0" data-testid="room-reply-cancel">
            <X size={14} aria-hidden="true" />
          </IconButton>
        </div>
      )}
      <div className="flex flex-col gap-1 rounded-3xl border border-border bg-bg-elevated py-1.5 pl-4 pr-1.5 transition-colors focus-within:border-border-strong">
        {mentions.length > 0 && (
          <ul className="m-0 flex list-none flex-wrap gap-1 p-0 pt-1" data-testid="room-mention-chips">
            {mentions.map((name) => (
              <li key={name} className="flex items-center gap-1 rounded-full bg-accent-subtle py-0.5 pl-2.5 pr-1 text-[13px] text-text-strong">
                <span className="max-w-[10rem] truncate">@{name === EVERYONE ? EVERYONE : label(name)}</span>
                <IconButton
                  aria-label={i18nT('pages.roomsPage.remove_mention', { name: name === EVERYONE ? EVERYONE : label(name) })}
                  onClick={() => setMentions((prev) => prev.filter((n) => n !== name))}
                  className="size-6 rounded-full p-0"
                >
                  <X size={12} aria-hidden="true" />
                </IconButton>
              </li>
            ))}
          </ul>
        )}
        <div className="flex items-end gap-2">
          <textarea
            ref={ref}
            value={value}
            rows={1}
            disabled={disabled}
            aria-label={i18nT('pages.roomsPage.message_label')}
            role="combobox"
            aria-expanded={open}
            aria-controls={open ? listId : undefined}
            aria-autocomplete="list"
            aria-activedescendant={open ? `${listId}-${active}` : undefined}
            placeholder={disabled ? disabledReason : i18nT('pages.roomsPage.message_placeholder', { name: roomName })}
            className="min-h-11 w-full min-w-0 flex-1 resize-none border-0 bg-transparent px-0 py-2.5 font-body text-sm text-text outline-hidden placeholder:text-muted disabled:opacity-60"
            onChange={(e) => {
              setValue(e.target.value)
              setDismissed(false)
              track(e.target)
            }}
            onSelect={(e) => track(e.currentTarget)}
            {...ime.bindComposition()}
            onKeyDown={(e) => {
              if (open) {
                if (e.key === 'ArrowDown') { e.preventDefault(); setSelected((active + 1) % candidates.length); return }
                if (e.key === 'ArrowUp') { e.preventDefault(); setSelected((active - 1 + candidates.length) % candidates.length); return }
                if (e.key === 'Enter') {
                  if (!ime.claimEnter(e)) return
                  e.preventDefault()
                  pick(candidates[active])
                  return
                }
                if (e.key === 'Tab') {
                  // A Tab the IME is still composing under is not a choice.
                  if (!ime.claimKey(e)) return
                  e.preventDefault()
                  pick(candidates[active])
                  return
                }
                if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); ime.reset(); setDismissed(true); return }
              }
              if (e.key === 'Backspace' && value === '' && mentions.length > 0) {
                setMentions((prev) => prev.slice(0, -1))
                return
              }
              if (e.key === 'Enter' && !e.shiftKey) {
                if (!ime.claimEnter(e)) return
                e.preventDefault()
                void send()
              }
            }}
            data-testid="room-message-input"
          />
          <IconButton
            type="submit"
            disabled={!canSend}
            aria-label={i18nT('pages.roomsPage.send')}
            className="mb-0.5 inline-flex size-10 shrink-0 items-center justify-center rounded-full bg-accent p-0 text-accent-fg hover:bg-accent-hover hover:text-accent-fg"
            data-testid="room-send"
          >
            <ArrowUp size={18} aria-hidden="true" />
          </IconButton>
        </div>
      </div>
    </form>
  )
}
