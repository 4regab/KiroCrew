/**
 * The Bot picker of the create-room form. A room holds two to six Bots, so a
 * seventh cannot be ticked: the unticked rows go inert once the room is full, and
 * the count line says where the selection stands. Names are the configured ones
 * (the identity the gateway routes by); a Bot's display label is only shown.
 */
import { useId } from 'react'
import type { MemberRosterRow } from '../../api/client/agents'
import { MAX_ROOM_MEMBERS, MIN_ROOM_MEMBERS } from '../../api/rooms'
import CrewAvatar from '../../components/CrewAvatar'
import { Checkbox } from '../../components/ui'
import { i18nT } from '../../i18n/t'
import { toggleMember } from './roomLogic'

export default function MemberPicker({ roster, selected, onChange }: {
  roster: readonly MemberRosterRow[]
  selected: readonly string[]
  onChange: (next: string[]) => void
}) {
  const labelId = useId()
  const full = selected.length >= MAX_ROOM_MEMBERS
  return (
    <div className="flex flex-col gap-2" data-testid="room-member-picker">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span id={labelId} className="text-sm font-medium text-text-strong">
          {i18nT('pages.roomsPage.members_label')}
        </span>
        <span className="text-[13px] text-muted" role="status" data-testid="room-member-count">
          {i18nT('pages.roomsPage.members_selected', { selected: selected.length, min: MIN_ROOM_MEMBERS, max: MAX_ROOM_MEMBERS })}
        </span>
      </div>
      {roster.length === 0 && (
        <p className="m-0 text-[13px] text-muted" data-testid="room-no-bots">
          {i18nT('pages.roomsPage.no_bots')}
        </p>
      )}
      <div
        role="group"
        aria-labelledby={labelId}
        className="flex max-h-[40vh] flex-col overflow-y-auto rounded-md border border-border"
      >
        {roster.map((row) => {
          const picked = selected.includes(row.name)
          const blocked = full && !picked
          return (
            <label
              key={row.name}
              className={`flex min-h-11 items-center gap-3 px-3 py-1.5 text-sm ${
                blocked ? 'cursor-not-allowed opacity-50' : 'cursor-pointer hover:bg-bg-hover'
              }`}
            >
              <Checkbox
                checked={picked}
                disabled={blocked}
                onChange={() => onChange(toggleMember(selected, row.name))}
                data-testid={`room-member-${row.name}`}
              />
              <CrewAvatar seed={row.name} avatar={row.avatar} size={20} className="shrink-0" />
              <span className="min-w-0 truncate text-text">{row.display_name || row.name}</span>
            </label>
          )
        })}
      </div>
      {full && (
        <p className="m-0 text-[13px] text-muted" data-testid="room-member-full">
          {i18nT('pages.roomsPage.members_full', { max: MAX_ROOM_MEMBERS })}
        </p>
      )}
    </div>
  )
}
