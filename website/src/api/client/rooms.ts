/**
 * Group chat rooms: the owner's list, create, read, rename, post and delete calls.
 *
 * The gateway's rooms are owner-only, and a gateway with `agent.group_rooms` off
 * refuses create and post with 403 `group_rooms_off`; reading still works. Both
 * failures throw through `j` like any other, and the page reads the code.
 */
import type { RoomDetail, RoomMessage, RoomSummary } from '../rooms'
import type { ClientTransport } from './transport'

export function createRoomsEndpoints({ get, post, patch, del, j }: ClientTransport) {
  const path = (id: string) => '/api/rooms/' + encodeURIComponent(id)
  return {
    requests: {
      rooms: {
        list: () => get('/api/rooms').then(j) as Promise<{ rooms: RoomSummary[] }>,
        get: (id: string, page: { before?: number; limit?: number } = {}) => {
          const query = new URLSearchParams()
          if (page.before) query.set('before', String(page.before))
          if (page.limit) query.set('limit', String(page.limit))
          const qs = query.toString()
          return get(path(id) + (qs ? `?${qs}` : '')).then(j) as Promise<{ room: RoomDetail }>
        },
        /** An empty `name` asks the gateway to name the room after its members. */
        create: (body: { name?: string; members: string[] }) =>
          post('/api/rooms', body).then(j) as Promise<{ ok: boolean; room: RoomSummary }>,
        rename: (id: string, name: string) =>
          patch(path(id), { name }).then(j) as Promise<{ ok: boolean; room: RoomSummary }>,
        post: (id: string, message: string, replyTo?: number) =>
          post(path(id) + '/messages', replyTo ? { message, reply_to: replyTo } : { message }).then(j) as Promise<{
            ok: boolean
            message: RoomMessage
            woke: string[]
          }>,
        remove: (id: string) => del(path(id)).then(j) as Promise<{ ok: boolean }>,
      },
    },
  }
}
