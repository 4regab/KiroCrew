/**
 * A room's face: the avatars of up to four of its Bots in a two-by-two cluster, drawn
 * with the same `CrewAvatar` every Bot wears. A room of two puts them on the diagonal and
 * a room of three leaves the last corner empty, so the cluster reads as "several".
 */
import type { MemberRosterRow } from '../../api/client/agents'
import CrewAvatar from '../../components/CrewAvatar'

const CORNERS = ['left-0 top-0', 'right-0 bottom-0', 'right-0 top-0', 'left-0 bottom-0'] as const

export default function RoomAvatar({ members, byName, size = 40 }: {
  members: readonly string[]
  byName: ReadonlyMap<string, MemberRosterRow>
  size?: number
}) {
  const face = Math.round(size * 0.62)
  return (
    <span
      className="relative inline-block shrink-0"
      style={{ width: size, height: size }}
      aria-hidden="true"
      data-testid="room-avatar"
    >
      {members.slice(0, 4).map((name, i) => (
        <CrewAvatar
          key={name}
          seed={name}
          avatar={byName.get(name)?.avatar}
          size={face}
          className={`absolute ${CORNERS[i]}`}
        />
      ))}
    </span>
  )
}
