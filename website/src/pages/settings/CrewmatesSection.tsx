/**
 * Settings > Developer > **Crewmates**: the server-side switches for Bots reaching
 * other Bots, group chat rooms and channels. `agent.bot_messaging`,
 * `agent.group_rooms` and `agent.channel_posts` are on by default. The reply-threads
 * switch sits in Feature Previews (`ReplyThreadsRow.tsx`).
 *
 * Sits under its own heading, right after Feature Previews. The heading is
 * visual only: config has no crewmate parent flag, so these toggles nest under a
 * section title rather than under a parent key.
 *
 * Server-side: each value is PATCHed to `agent.<switch>` and the shared
 * `['kirocrewConfig']` query is refetched, so the switch reads the saved value.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import ErrorNotice from '../../components/ErrorNotice'
import { Btn } from '../../components/ui'
import { SettingsCard, SettingsSection, SettingsToggle } from '../../components/settings'
import { i18nT } from '../../i18n/t'

type KirocrewConfigShape = {
  agent?: { bot_messaging?: boolean; group_rooms?: boolean; channel_posts?: boolean }
}

/** The gateway-side switches for Bots reaching other people and places. All three are on by
 *  default, so a config that does not name one reads as on. The gateway reads each on every
 *  call, so a change needs no restart. */
type AgentSwitch = 'bot_messaging' | 'group_rooms' | 'channel_posts'

export function CrewmatesSection() {
  const qc = useQueryClient()
  const cfgQ = useQuery<KirocrewConfigShape>({
    queryKey: ['kirocrewConfig'],
    queryFn: () => api.kirocrewConfig(),
  })
  const [saveError, setSaveError] = useState('')
  const on = (key: AgentSwitch) => cfgQ.data?.agent?.[key] !== false
  const agentMut = useMutation({
    mutationFn: ({ key, value }: { key: AgentSwitch; value: boolean }) => api.patchConfig(`agent.${key}`, value),
    onSuccess: () => {
      setSaveError('')
      void qc.invalidateQueries({ queryKey: ['kirocrewConfig'] })
    },
    onError: () => setSaveError(i18nT('pages.settings.crewmatesSection.failed_to_save')),
  })

  return (
    <SettingsSection title={i18nT('pages.settings.crewmatesSection.title')}>
      {/* Sits right after Feature Previews, so the line says what kind of group
          this is: server settings that apply on every device, not previews. */}
      <p className="text-[13px] text-muted mb-2" data-testid="crewmates-section-description">
        {i18nT('pages.settings.crewmatesSection.description')}
      </p>
      {/* A config read that failed leaves the switch disabled; say so, with the
          query's own retry, rather than showing a control that cannot move.
          askAgent: a toggle holds no draft, so the hand-off loses nothing. */}
      {cfgQ.isError && (
        <ErrorNotice
          message={i18nT('pages.settings.crewmatesSection.failed_to_load')}
          askAgent
          className="mb-2"
          testId="crewmates-config-error"
          footer={
            <Btn onClick={() => { void cfgQ.refetch() }} disabled={cfgQ.isFetching} data-testid="crewmates-config-retry">
              {i18nT('pages.settings.crewmatesSection.retry')}
            </Btn>
          }
        />
      )}
      {saveError && (
        <ErrorNotice
          message={saveError}
          onDismiss={() => setSaveError('')}
          askAgent
          className="mb-2"
          testId="crewmates-save-error"
        />
      )}
      <SettingsCard>
        <SettingsToggle
          label={i18nT('pages.settings.crewmatesSection.bot_messaging')}
          hint={i18nT('pages.settings.crewmatesSection.bot_messaging_desc')}
          checked={on('bot_messaging')}
          onChange={(v) => agentMut.mutate({ key: 'bot_messaging', value: v })}
          disabled={!cfgQ.isSuccess || agentMut.isPending}
          configKey="agent.bot_messaging"
        />
        <SettingsToggle
          label={i18nT('pages.settings.crewmatesSection.group_rooms')}
          hint={i18nT('pages.settings.crewmatesSection.group_rooms_desc')}
          checked={on('group_rooms')}
          onChange={(v) => agentMut.mutate({ key: 'group_rooms', value: v })}
          disabled={!cfgQ.isSuccess || agentMut.isPending}
          configKey="agent.group_rooms"
        />
        <SettingsToggle
          label={i18nT('pages.settings.crewmatesSection.channel_posts')}
          hint={i18nT('pages.settings.crewmatesSection.channel_posts_desc')}
          checked={on('channel_posts')}
          onChange={(v) => agentMut.mutate({ key: 'channel_posts', value: v })}
          disabled={!cfgQ.isSuccess || agentMut.isPending}
          configKey="agent.channel_posts"
        />
      </SettingsCard>
    </SettingsSection>
  )
}
