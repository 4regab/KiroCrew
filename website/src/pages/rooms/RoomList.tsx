/**
 * The room list: one link per room, the open one marked. Each row is a real link
 * to `/rooms/:id`, so the back button, a reload and a copied address all land on
 * the same room.
 */
import { Link } from 'react-router-dom'
import { MessagesSquare, Plus } from 'lucide-react'
import type { RoomSummary } from '../../api/rooms'
import { Btn, ContentSkeleton, EmptyState } from '../../components/ui'
import ErrorNotice from '../../components/ErrorNotice'
import { fmtList } from '../../i18n/format'
import { i18nT } from '../../i18n/t'

export default function RoomList({ rooms, loading, error, selectedId, canCreate, onCreate, onRetry }: {
  rooms: readonly RoomSummary[]
  loading: boolean
  error: string
  selectedId: string
  canCreate: boolean
  onCreate: () => void
  onRetry: () => void
}) {
  if (error) {
    return (
      <ErrorNotice
        message={error}
        askAgent
        testId="room-list-error"
        footer={<Btn onClick={onRetry}>{i18nT('pages.roomsPage.retry')}</Btn>}
      />
    )
  }
  if (loading) return <ContentSkeleton rows={4} />
  if (rooms.length === 0) {
    return (
      <EmptyState
        icon={<MessagesSquare size={20} />}
        title={i18nT('pages.roomsPage.no_rooms')}
        subtitle={i18nT('pages.roomsPage.no_rooms_hint')}
        action={
          canCreate ? (
            <Btn primary onClick={onCreate} data-testid="room-empty-create">
              <Plus size={14} aria-hidden="true" /> {i18nT('pages.roomsPage.new_room')}
            </Btn>
          ) : undefined
        }
        testId="rooms-empty"
      />
    )
  }
  return (
    <nav aria-label={i18nT('pages.roomsPage.list_label')}>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0" data-testid="room-list">
        {rooms.map((room) => {
          const current = room.id === selectedId
          return (
            <li key={room.id}>
              <Link
                to={`/rooms/${encodeURIComponent(room.id)}`}
                aria-current={current ? 'page' : undefined}
                className={`flex min-h-14 flex-col justify-center gap-0.5 rounded-lg border px-3 py-2 no-underline transition-colors focus-ring ${
                  current ? 'border-accent bg-accent-subtle' : 'border-border bg-card hover:bg-bg-hover'
                }`}
                data-testid="room-list-item"
              >
                <span className="truncate text-sm font-medium text-text-strong">{room.name}</span>
                <span className="truncate text-[13px] text-muted">{fmtList(room.members)}</span>
              </Link>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
