/**
 * Settings > Developer > Crewmates -- the Bot switches (messaging, group rooms,
 * channel posts). Each is on by default; a switch PATCHes its config key and the
 * shared config query is refetched; a refused save says so.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'

vi.mock('../../api/client', () => ({
  api: {
    kirocrewConfig: vi.fn(),
    patchConfig: vi.fn(),
  },
}))

import { api } from '../../api/client'
import { CrewmatesSection } from './CrewmatesSection'

let qc: QueryClient
const wrap = (ui: React.ReactElement) => render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)

beforeEach(() => {
  vi.clearAllMocks()
  qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  vi.mocked(api.kirocrewConfig).mockResolvedValue({})
  vi.mocked(api.patchConfig).mockResolvedValue({ ok: true })
})

describe('CrewmatesSection', () => {
  it('says under the heading that these are server settings, not previews', async () => {
    wrap(<CrewmatesSection />)
    expect(await screen.findByTestId('crewmates-section-description')).toHaveTextContent(
      "Server settings for Bots, not previews. They're saved on the server, so they apply on every device.",
    )
  })

  it('renders the bot-messaging switch on by default and saves the value on toggle', async () => {
    wrap(<CrewmatesSection />)
    const toggle = await screen.findByRole('switch', { name: /Bots messaging each other/ })
    await waitFor(() => expect(toggle).not.toHaveAttribute('aria-disabled'))
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(toggle)
    await waitFor(() => expect(api.patchConfig).toHaveBeenCalledWith('agent.bot_messaging', false))
    // The shared config query is refetched, so the Members page's hook sees the new value.
    await waitFor(() => expect(api.kirocrewConfig).toHaveBeenCalledTimes(2))
  })

  it('a failed config read is said, with a retry that re-reads and enables the switch', async () => {
    vi.mocked(api.kirocrewConfig).mockRejectedValueOnce(new Error('boom'))
    wrap(<CrewmatesSection />)
    await screen.findByTestId('crewmates-config-error')
    expect(screen.getByText("Couldn't load the bot settings.")).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: /Bots messaging each other/ })).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(screen.getByTestId('crewmates-config-retry'))
    await waitFor(() => expect(screen.queryByTestId('crewmates-config-error')).toBeNull())
    await waitFor(() => expect(screen.getByRole('switch', { name: /Bots messaging each other/ })).not.toHaveAttribute('aria-disabled'))
  })

  it('a refused save is said in plain words and the switch stays where the server has it', async () => {
    vi.mocked(api.patchConfig).mockRejectedValue(new Error('boom'))
    wrap(<CrewmatesSection />)
    const toggle = await screen.findByRole('switch', { name: /Bots messaging each other/ })
    await waitFor(() => expect(toggle).not.toHaveAttribute('aria-disabled'))
    fireEvent.click(toggle)
    await screen.findByText("Couldn't save the bot setting. Try again.")
    expect(toggle).toHaveAttribute('aria-checked', 'true')
  })
})
