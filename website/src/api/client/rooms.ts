/**
 * Group chat rooms: the owner's list, create, read, post and delete calls.
 *
 * The gateway's rooms are owner-only, and a gateway with `agent.group_rooms` off
 * refuses create and post with 403 `group_rooms_off`; reading still works. Both
 * failures throw through `j` like any other, and the page reads the code.
 */
import type { RoomDetail, RoomMessage, RoomSummary } from '../rooms'
import type { ClientTransport } from './transport'

export function createRoomsEndpoints({ get, post, del, j }: ClientTransport) {
  const path = (id: string) => '/api/rooms/' + encodeURIComponent(id)
  return {
    requests: {
      rooms: {
        list: () => get('/api/rooms').then(j) as Promise<{ rooms: RoomSummary[] }>,
        get: (id: string, before?: number) =>
          get(path(id) + (before ? `?before=${before}` : '')).then(j) as Promise<{ room: RoomDetail }>,
        create: (body: { name: string; members: string[] }) =>
          post('/api/rooms', body).then(j) as Promise<{ ok: boolean; room: RoomSummary }>,
        post: (id: string, message: string) =>
          post(path(id) + '/messages', { message }).then(j) as Promise<{
            ok: boolean
            message: RoomMessage
            woke: string[]
          }>,
        remove: (id: string) => del(path(id)).then(j) as Promise<{ ok: boolean }>,
      },
    },
  }
}
