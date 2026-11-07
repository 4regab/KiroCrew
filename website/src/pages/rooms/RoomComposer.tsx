/**
 * The user's text box for a room. Typing `@` offers the room's own Bots and
 * `@everyone`; the list is a combobox (arrow keys move, Enter or Tab picks, Escape
 * closes it). The gateway wakes every Bot on every message, so a mention is a
 * cue to the Bots and not a routing rule. Nothing is cleared until the gateway
 * took the message, so a failed send keeps the draft.
 */
import { useId, useMemo, useRef, useState } from 'react'
import { Send } from 'lucide-react'
import { MAX_ROOM_MESSAGE_CHARS } from '../../api/rooms'
import { Btn } from '../../components/ui'
import { useAutoGrowTextarea } from '../../hooks/useAutoGrowTextarea'
import { useImeGuard } from '../../hooks/useImeGuard'
import { fmtNumber } from '../../i18n/format'
import { i18nT } from '../../i18n/t'
import { applyMention, EVERYONE, mentionCandidates, mentionQuery } from './roomLogic'

/** The counter turns live (announced) this close to the cap. */
const NEAR_LIMIT_CHARS = 100

export default function RoomComposer({ members, disabled, disabledReason, sending, onSend }: {
  members: readonly string[]
  disabled: boolean
  /** Why typing is off, shown as the placeholder. */
  disabledReason: string
  sending: boolean
  /** Resolves true when the gateway took the message; the draft is cleared only then. */
  onSend: (text: string) => Promise<boolean>
}) {
  const [value, setValue] = useState('')
  const [caret, setCaret] = useState(0)
  const [selected, setSelected] = useState(0)
  const [dismissed, setDismissed] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)
  const ime = useImeGuard()
  const listId = useId()
  const counterId = useId()
  useAutoGrowTextarea(ref, value, Math.round(window.innerHeight * 0.3))

  const query = dismissed ? null : mentionQuery(value, caret)
  const candidates = useMemo(() => (query ? mentionCandidates(members, query.query) : []), [members, query])
  const open = candidates.length > 0
  const active = Math.min(selected, Math.max(candidates.length - 1, 0))

  const over = value.length > MAX_ROOM_MESSAGE_CHARS
  const near = value.length >= MAX_ROOM_MESSAGE_CHARS - NEAR_LIMIT_CHARS
  const canSend = !disabled && !sending && value.trim().length > 0 && !over

  const track = (el: HTMLTextAreaElement) => {
    setCaret(el.selectionStart ?? el.value.length)
    setSelected(0)
  }

  const pick = (name: string) => {
    if (!query) return
    const next = applyMention(value, caret, query.start, name)
    setValue(next.text)
    setCaret(next.caret)
    // Put the caret after the inserted name once React has written the new value.
    requestAnimationFrame(() => ref.current?.setSelectionRange(next.caret, next.caret))
    ref.current?.focus()
  }

  const send = async () => {
    if (!canSend) return
    if (await onSend(value.trim())) {
      setValue('')
      setCaret(0)
    }
  }

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
          className="absolute bottom-full left-0 z-10 mb-1 max-h-48 w-64 max-w-full overflow-y-auto rounded-lg border border-border bg-bg-elevated p-1 shadow-lg"
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
              className={`flex min-h-9 cursor-pointer items-center rounded-md px-3 text-sm ${
                i === active ? 'bg-accent text-accent-fg' : 'text-text hover:bg-bg-hover'
              }`}
            >
              {name === EVERYONE ? i18nT('pages.roomsPage.mention_everyone') : name}
            </div>
          ))}
        </div>
      )}
      <div className="flex items-end gap-2">
        <textarea
          ref={ref}
          value={value}
          rows={1}
          disabled={disabled}
          aria-label={i18nT('pages.roomsPage.message_label')}
          aria-describedby={counterId}
          aria-invalid={over}
          role="combobox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          aria-autocomplete="list"
          aria-activedescendant={open ? `${listId}-${active}` : undefined}
          placeholder={disabled ? disabledReason : i18nT('pages.roomsPage.message_placeholder')}
          className="min-h-11 w-full min-w-0 flex-1 resize-none rounded-md border border-border bg-bg-elevated px-3 py-2 font-body text-sm text-text outline-hidden transition-colors focus-ring disabled:opacity-60"
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
            if (e.key === 'Enter' && !e.shiftKey) {
              if (!ime.claimEnter(e)) return
              e.preventDefault()
              void send()
            }
          }}
          data-testid="room-message-input"
        />
        <Btn
          primary
          type="submit"
          disabled={!canSend}
          aria-label={i18nT('pages.roomsPage.send')}
          className="min-h-11 min-w-11 justify-center"
          data-testid="room-send"
        >
          <Send size={16} aria-hidden="true" />
          <span className="hidden md:inline">{i18nT('pages.roomsPage.send')}</span>
        </Btn>
      </div>
      <div
        id={counterId}
        aria-live={near ? 'polite' : 'off'}
        className={`text-right text-[13px] ${over ? 'text-danger-text' : 'text-muted'}`}
        data-testid="room-char-count"
      >
        {i18nT('pages.roomsPage.char_count', { used: fmtNumber(value.length), max: fmtNumber(MAX_ROOM_MESSAGE_CHARS) })}
      </div>
    </form>
  )
}
