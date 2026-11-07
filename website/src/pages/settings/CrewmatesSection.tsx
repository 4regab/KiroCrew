/**
 * Settings > Developer > **Crewmates**: the server-side switches for crewmate
 * features. `dashboard.crewmate_threads`, reply threads on crewmate chat messages
 * (RFC crewmates launch, screen 07), is off by default. `agent.bot_messaging`,
 * `agent.group_rooms` and `agent.channel_posts`, which let Bots message each other,
 * join group chat rooms and ask to post to a channel, are on by default.
 *
 * Sits under its own heading, right after Feature Previews. The heading is
 * visual only: config has no crewmate parent flag, so this toggle nests under a
 * section title rather than under a parent key.
 *
 * Server-side, like the Power toggle on the Chat tab: the value is PATCHed to
 * `dashboard.crewmate_threads` and read back through the shared
 * `['kirocrewConfig']` query the Members page reads the flag from
 * (`useCrewmateThreadsFlag`), so the Reply in thread control appears or goes
 * on the next render without a reload.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import ErrorNotice from '../../components/ErrorNotice'
import { Btn } from '../../components/ui'
import { SettingsCard, SettingsSection, SettingsToggle } from '../../components/settings'
import { CREWMATE_THREADS_CONFIG_KEY } from '../../hooks/useCrewmateThreadsFlag'
import { i18nT } from '../../i18n/t'

type KirocrewConfigShape = {
  dashboard?: { crewmate_threads?: boolean }
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
  const threads = cfgQ.data?.dashboard?.crewmate_threads === true
  const threadsMut = useMutation({
    mutationFn: (v: boolean) => api.patchConfig(CREWMATE_THREADS_CONFIG_KEY, v),
    onSuccess: () => {
      setSaveError('')
      void qc.invalidateQueries({ queryKey: ['kirocrewConfig'] })
    },
    onError: () => setSaveError(i18nT('pages.settings.crewmatesSection.failed_to_save')),
  })

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
          label={i18nT('pages.settings.crewmatesSection.reply_threads')}
          hint={i18nT('pages.settings.crewmatesSection.reply_threads_desc')}
          checked={threads}
          onChange={(v) => threadsMut.mutate(v)}
          disabled={!cfgQ.isSuccess || threadsMut.isPending}
          configKey={CREWMATE_THREADS_CONFIG_KEY}
        />
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
