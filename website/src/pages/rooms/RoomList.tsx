/**
 * The room list: one row per room, like any chat in a messenger. A row is a real link to
 * `/rooms/:id` (so the back button, a reload and a copied address land on the same
 * room) with the room's face, its name and one line of its newest message. A right-click,
 * a long press or the menu key opens Rename and Delete.
 *
 * ponytail: the list answer carries no preview, so each room's newest message is read
 * with one `GET /api/rooms/{id}?limit=1` that runs again only when the room's `seq`
 * changes. A gateway at its 20 rooms makes 20 small reads on the first load. Upgrade
 * path: a `last` field on the list answer.
 */
import { Link } from 'react-router-dom'
import { useQueries } from '@tanstack/react-query'
import { MessagesSquare, Pencil, Plus, Trash2 } from 'lucide-react'
import { api } from '../../api/client'
import type { MemberRosterRow } from '../../api/client/agents'
import type { RoomMessage, RoomSummary } from '../../api/rooms'
import { Btn, ContentSkeleton, EmptyState } from '../../components/ui'
import ErrorNotice from '../../components/ErrorNotice'
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuTrigger } from '../../components/ui/context-menu'
import { i18nT } from '../../i18n/t'
import RoomAvatar from './RoomAvatar'

export default function RoomList({ rooms, byName, loading, error, selectedId, canCreate, onCreate, onRetry, onRename, onDelete }: {
  rooms: readonly RoomSummary[]
  byName: ReadonlyMap<string, MemberRosterRow>
  loading: boolean
  error: string
  selectedId: string
  canCreate: boolean
  onCreate: () => void
  onRetry: () => void
  onRename: (room: RoomSummary) => void
  onDelete: (room: RoomSummary) => void
}) {
  const previews = useQueries({
    queries: rooms.map((room) => ({
      queryKey: ['rooms', room.id, 'last', room.seq] as const,
      queryFn: async (): Promise<RoomMessage | null> => (await api.rooms.get(room.id, { limit: 1 })).room.messages[0] ?? null,
      enabled: room.seq > 0,
      staleTime: Infinity,
    })),
  })

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
              <Plus size={14} aria-hidden="true" /> {i18nT('pages.roomsPage.new_group')}
            </Btn>
          ) : undefined
        }
        testId="rooms-empty"
      />
    )
  }
  return (
    <nav aria-label={i18nT('pages.roomsPage.list_label')}>
      <ul className="m-0 flex list-none flex-col gap-0.5 p-0" data-testid="room-list">
        {rooms.map((room, i) => {
          const current = room.id === selectedId
          const last = previews[i]?.data
          return (
            <li key={room.id}>
              {/* Not modal: the menu hands over to a dialog (rename, delete), and a modal menu's page lock is
                  still on when that dialog closes, which leaves the page unclickable. */}
              <ContextMenu modal={false}>
                <ContextMenuTrigger asChild>
                  <Link
                    to={`/rooms/${encodeURIComponent(room.id)}`}
                    aria-current={current ? 'page' : undefined}
                    className={`flex min-h-14 items-center gap-3 rounded-xl px-2.5 py-2 no-underline transition-colors focus-ring ${
                      current ? 'bg-bg-hover' : 'hover:bg-bg-hover'
                    }`}
                    data-testid="room-list-item"
                  >
                    <RoomAvatar members={room.members} byName={byName} size={40} />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate text-sm font-medium text-text-strong">{room.name}</span>
                      <span className="truncate text-[13px] text-muted" data-testid="room-list-preview">
                        {last ? last.text.replace(/\s+/g, ' ') : ''}
                      </span>
                    </span>
                  </Link>
                </ContextMenuTrigger>
                <ContextMenuContent className="min-w-[200px]">
                  <ContextMenuItem className="gap-2" disabled={!canCreate} onSelect={() => onRename(room)} data-testid="room-menu-rename">
                    <Pencil size={14} aria-hidden="true" /> {i18nT('pages.roomsPage.rename_chat')}
                  </ContextMenuItem>
                  <ContextMenuItem className="gap-2 text-danger" onSelect={() => onDelete(room)} data-testid="room-menu-delete">
                    <Trash2 size={14} aria-hidden="true" /> {i18nT('pages.roomsPage.delete')}
                  </ContextMenuItem>
                </ContextMenuContent>
              </ContextMenu>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
