/**
 * RoomsPage — group chat rooms: the user and two to six Bots in one room the
 * gateway owns (`/api/rooms`, `kiro_crew/dashboard/group_rooms.py`).
 *
 * Mobile first. Below `md` the page shows either the room list or one room, and a
 * back arrow in the room's header returns to the list; from `md` up the two sit
 * side by side and scroll on their own. The choice is CSS on one tree, not two
 * components, so a window that crosses 768px (a fold, a rotation) keeps the open
 * room and the half-typed message.
 *
 * Rooms are not chat sessions: a Bot reads a room only through the turn the
 * gateway starts in its own thread. The page reads the room list and the open
 * room on a timer and posts the user's own messages; it never speaks for a Bot.
 */
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { motion, useReducedMotion } from 'framer-motion'
import { api } from '../api/client'
import { membersRosterQuery } from '../api/membersQuery'
import { ROOM_LIST_POLL_MS, roomsQueryKey, type RoomSummary } from '../api/rooms'
import ErrorNotice from '../components/ErrorNotice'
import { Btn, PageHeader } from '../components/ui'
import { i18nT } from '../i18n/t'
import CreateRoomDialog from './rooms/CreateRoomDialog'
import RoomConversation from './rooms/RoomConversation'
import RoomList from './rooms/RoomList'
import { errText } from './rooms/roomLogic'

type KirocrewConfigShape = { agent?: { group_rooms?: boolean } }

export default function RoomsPage() {
  const { roomId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const reduceMotion = useReducedMotion()
  const [creating, setCreating] = useState(false)
  // Set when the gateway answers 403 `group_rooms_off`; the config read below covers the
  // switch being off before anything was tried.
  const [offFromGateway, setOffFromGateway] = useState(false)

  const listQ = useQuery({
    queryKey: roomsQueryKey(),
    queryFn: async () => (await api.rooms.list()).rooms,
    refetchInterval: ROOM_LIST_POLL_MS,
  })
  const rosterQ = useQuery(membersRosterQuery)
  const cfgQ = useQuery<KirocrewConfigShape>({ queryKey: ['kirocrewConfig'], queryFn: () => api.kirocrewConfig() })
  const roomsOff = offFromGateway || cfgQ.data?.agent?.group_rooms === false
  // Only a Bot with a pinned thread can be woken, so only those can be picked.
  const roster = (rosterQ.data ?? []).filter((r) => r.slot_key)

  const open = (room: RoomSummary) => {
    setCreating(false)
    void queryClient.invalidateQueries({ queryKey: roomsQueryKey() })
    navigate(`/rooms/${encodeURIComponent(room.id)}`)
  }
  const toList = () => navigate('/rooms')

  return (
    <>
      {/* On a phone the open room carries its own header (back arrow and name), so the page
          header gives its row to the conversation. */}
      <div className={roomId ? 'hidden md:block' : undefined}>
        <PageHeader
          title={i18nT('pages.roomsPage.title')}
          subtitle={i18nT('pages.roomsPage.subtitle')}
          actions={
            <Btn
              primary
              onClick={() => setCreating(true)}
              disabled={roomsOff}
              data-testid="room-new"
            >
              <Plus size={14} aria-hidden="true" /> {i18nT('pages.roomsPage.new_room')}
            </Btn>
          }
        />
      </div>
      {roomsOff && (
        <div className="px-4 pb-2 md:px-6">
          <ErrorNotice message={i18nT('pages.roomsPage.rooms_off')} askAgent testId="rooms-off-notice" />
        </div>
      )}
      <div className="flex min-h-0 flex-1 gap-4 px-4 pb-4 md:px-6" data-testid="rooms-page">
        <aside
          className={`min-h-0 w-full shrink-0 overflow-y-auto md:block md:w-72 lg:w-80 ${roomId ? 'hidden' : 'block'}`}
          data-testid="rooms-list-pane"
        >
          <RoomList
            rooms={listQ.data ?? []}
            loading={listQ.isLoading}
            error={listQ.isError ? errText(listQ.error) : ''}
            selectedId={roomId}
            canCreate={!roomsOff}
            onCreate={() => setCreating(true)}
            onRetry={() => void listQ.refetch()}
          />
        </aside>
        <section
          className={`min-h-0 min-w-0 flex-1 flex-col md:flex ${roomId ? 'flex' : 'hidden'}`}
          aria-label={i18nT('pages.roomsPage.conversation_label')}
          data-testid="rooms-conversation-pane"
        >
          {roomId ? (
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
                onDeleted={toList}
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
      <CreateRoomDialog
        open={creating}
        onOpenChange={setCreating}
        roster={roster}
        rosterFailed={rosterQ.isError}
        roomsOff={roomsOff}
        onCreated={open}
        onRoomsOff={() => setOffFromGateway(true)}
      />
    </>
  )
}
