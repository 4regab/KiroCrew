/**
 * RoomsPage — group chat rooms: the user and two to six Bots in one room the
 * gateway owns (`/api/rooms`, `kiro_crew/dashboard/group_rooms.py`).
 *
 * Laid out like a messenger. The left list has one row per room (a cluster of the Bots'
 * faces, the name, the newest line); `+` or Ctrl/Cmd+Shift+N opens a `To:` field that
 * makes a new group, and a room's name is changed from its header pill or the row's menu.
 *
 * Mobile first. Below `md` the page shows either the room list or one room, and a
 * back arrow in the room's header returns to the list; from `md` up the two sit
 * side by side and scroll on their own. The choice is CSS on one tree, not two
 * components, so a window that crosses 768px (a fold, a rotation) keeps the open
 * room and the half-typed message.
 *
 * Rooms are not chat sessions: a Bot reads a room only through the turn the
 * gateway starts in its own thread. The page reads the room list, the open room and
 * the Bots' working state on a timer and posts the user's own messages; it never speaks
 * for a Bot.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { motion, useReducedMotion } from 'framer-motion'
import { api } from '../api/client'
import { membersRosterQuery } from '../api/membersQuery'
import { ROOM_LIST_POLL_MS, ROOM_POLL_MS, roomsQueryKey, type RoomSummary } from '../api/rooms'
import { useConfirm } from '../components/ConfirmDialog'
import ErrorNotice from '../components/ErrorNotice'
import { IconButton, PageHeader } from '../components/ui'
import { i18nT } from '../i18n/t'
import NewRoomPane from './rooms/NewRoomPane'
import RoomConversation from './rooms/RoomConversation'
import RoomDetailsDialog from './rooms/RoomDetailsDialog'
import RoomList from './rooms/RoomList'
import { errText } from './rooms/roomLogic'

type KirocrewConfigShape = { agent?: { group_rooms?: boolean } }

/** The path segment that opens the new-group field; a room id is 16 hex digits, so it cannot collide. */
const NEW = 'new'

export default function RoomsPage() {
  const { roomId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const reduceMotion = useReducedMotion()
  const { confirm, confirmDialog } = useConfirm()
  const [details, setDetails] = useState<RoomSummary | null>(null)
  const [deleteError, setDeleteError] = useState('')
  // Set when the gateway answers 403 `group_rooms_off`; the config read below covers the
  // switch being off before anything was tried.
  const [offFromGateway, setOffFromGateway] = useState(false)

  const listQ = useQuery({
    queryKey: roomsQueryKey(),
    queryFn: async () => (await api.rooms.list()).rooms,
    refetchInterval: ROOM_LIST_POLL_MS,
  })
  // `running` on a roster row is what says a Bot is working, so the roster is re-read as often as the open room.
  const rosterQ = useQuery({ ...membersRosterQuery, refetchInterval: ROOM_POLL_MS })
  const cfgQ = useQuery<KirocrewConfigShape>({ queryKey: ['kirocrewConfig'], queryFn: () => api.kirocrewConfig() })
  const roomsOff = offFromGateway || cfgQ.data?.agent?.group_rooms === false
  // Only a Bot with a pinned thread can be woken, so only those can be picked.
  const roster = useMemo(() => (rosterQ.data ?? []).filter((r) => r.slot_key), [rosterQ.data])
  const byName = useMemo(() => new Map(roster.map((r) => [r.name, r])), [roster])
  const creating = roomId === NEW

  const toList = () => navigate('/rooms')
  const toNew = () => navigate(`/rooms/${NEW}`)
  const open = (room: RoomSummary) => {
    void queryClient.invalidateQueries({ queryKey: roomsQueryKey() })
    navigate(`/rooms/${encodeURIComponent(room.id)}`)
  }

  // Ctrl/Cmd+Shift+N starts a new group (a browser may keep the chord for itself). Heard before the
  // shell's own shortcuts (Ctrl/Cmd+N is a new chat) so only this page claims the chord.
  useEffect(() => {
    if (roomsOff) return
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && !e.altKey && e.key.toLowerCase() === 'n') {
        e.preventDefault()
        e.stopPropagation()
        navigate(`/rooms/${NEW}`)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [roomsOff, navigate])

  const remove = useMutation({
    mutationFn: (room: RoomSummary) => api.rooms.remove(room.id),
    onSuccess: (_done, room) => {
      void queryClient.invalidateQueries({ queryKey: roomsQueryKey() })
      setDetails(null)
      if (room.id === roomId) toList()
    },
    onError: (e) => setDeleteError(errText(e)),
  })
  const askDelete = async (room: RoomSummary) => {
    const ok = await confirm({
      title: i18nT('pages.roomsPage.delete_title'),
      body: i18nT('pages.roomsPage.delete_body'),
      confirmLabel: i18nT('pages.roomsPage.delete'),
    })
    if (ok) remove.mutate(room)
  }

  return (
    <>
      {/* On a phone the open room carries its own header (back arrow and name), so the page
          header gives its row to the conversation. */}
      <div className={roomId ? 'hidden md:block' : undefined}>
        <PageHeader title={i18nT('pages.roomsPage.title')} subtitle={i18nT('pages.roomsPage.subtitle')} />
      </div>
      {roomsOff && (
        <div className="px-4 pb-2 md:px-6">
          <ErrorNotice message={i18nT('pages.roomsPage.rooms_off')} askAgent testId="rooms-off-notice" />
        </div>
      )}
      {deleteError && (
        <div className="px-4 pb-2 md:px-6">
          <ErrorNotice message={deleteError} onDismiss={() => setDeleteError('')} askAgent testId="room-delete-error" />
        </div>
      )}
      <div className="flex min-h-0 flex-1 gap-4 px-4 pb-4 md:px-6" data-testid="rooms-page">
        <aside
          className={`min-h-0 w-full shrink-0 flex-col gap-2 overflow-y-auto md:flex md:w-72 lg:w-80 ${roomId ? 'hidden' : 'flex'}`}
          data-testid="rooms-list-pane"
        >
          <div className="flex justify-end">
            <IconButton
              aria-label={i18nT('pages.roomsPage.new_group')}
              aria-keyshortcuts="Control+Shift+N"
              onClick={toNew}
              disabled={roomsOff}
              className="min-h-11 min-w-11 rounded-full"
              data-testid="room-new"
            >
              <Plus size={18} aria-hidden="true" />
            </IconButton>
          </div>
          <RoomList
            rooms={listQ.data ?? []}
            byName={byName}
            loading={listQ.isLoading}
            error={listQ.isError ? errText(listQ.error) : ''}
            selectedId={roomId}
            canCreate={!roomsOff}
            onCreate={toNew}
            onRetry={() => void listQ.refetch()}
            onRename={setDetails}
            onDelete={(room) => void askDelete(room)}
          />
        </aside>
        <section
          className={`min-h-0 min-w-0 flex-1 flex-col md:flex ${roomId ? 'flex' : 'hidden'}`}
          aria-label={i18nT('pages.roomsPage.conversation_label')}
          data-testid="rooms-conversation-pane"
        >
          {creating ? (
            <NewRoomPane
              roster={roster}
              rosterFailed={rosterQ.isError}
              roomsOff={roomsOff}
              onCreated={open}
              onClose={toList}
              onRoomsOff={() => setOffFromGateway(true)}
            />
          ) : roomId ? (
            // One element that slides in when a room opens, so on a phone the move from the list
            // reads as going into the room. Reduced motion draws it in place.
            <motion.div
              key={roomId}
              className="flex min-h-0 min-w-0 flex-1 flex-col"
              initial={reduceMotion ? false : { opacity: 0, x: 16 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.18, ease: 'easeOut' }}
            >
              <RoomConversation
                roomId={roomId}
                roster={roster}
                roomsOff={roomsOff}
                onBack={toList}
                onOpenDetails={setDetails}
                onRoomsOff={() => setOffFromGateway(true)}
              />
            </motion.div>
          ) : (
            <p className="m-0 hidden p-6 text-center text-sm text-muted md:block" data-testid="rooms-pick-one">
              {i18nT('pages.roomsPage.pick_a_room')}
            </p>
          )}
        </section>
      </div>
      <RoomDetailsDialog
        room={details}
        byName={byName}
        roomsOff={roomsOff}
        onClose={() => setDetails(null)}
        // The details close first: the confirmation is a dialog of its own, and two modals do not stack.
        onDelete={(room) => {
          setDetails(null)
          void askDelete(room)
        }}
        onRoomsOff={() => setOffFromGateway(true)}
      />
      {confirmDialog}
    </>
  )
}
