import { useState, type ReactNode } from 'react'
import ErrorBoundary from '../../components/ErrorBoundary'
import { useLoadedInstalledApps } from '../../hooks/panelTabRegistry'
import { pathBasename } from '../../utils/pathBasename'
import { getDocumentPanelTabs, type DocumentPanelTarget } from './documentPanelTabs'

/**
 * The tabs an edition registered with `registerDocumentPanelTabs`, in a strip
 * above an opened file or artifact. The first tab is the document itself, named
 * by its file name or slug; the others are the edition's components, each in its
 * own ErrorBoundary so a throwing tab shows the standard crash card in its place.
 *
 * The document stays mounted (hidden) while another tab is selected, so an
 * unsaved buffer and its scroll position survive the switch. With no visible
 * registration the document renders alone, exactly as in the stock build.
 *
 * App enable state is read from the `['apps']` cache without fetching: the shell
 * already loads that list, and until it lands a tab that requires an app stays
 * hidden rather than flashing in.
 */
export function DocumentPanelTabs({
  target,
  active,
  slot,
  children,
}: {
  target: DocumentPanelTarget
  active: boolean
  slot: string
  children: ReactNode
}) {
  // `undefined` until the shell's own read has cached the list.
  const apps = useLoadedInstalledApps() ?? []
  const [selected, setSelected] = useState<string | null>(null)
  const tabs = getDocumentPanelTabs().filter(t => {
    if (!t.requiresApp) return true
    const app = apps.find(a => a.name === t.requiresApp) as { enabled?: boolean; unavailable?: unknown } | undefined
    return !!app?.enabled && !app.unavailable
  })
  if (tabs.length === 0) return <>{children}</>
  const current = tabs.find(t => t.id === selected)
  const docName = target.kind === 'file' ? pathBasename(target.path) || target.path : target.slug
  const tabCls = (on: boolean) =>
    `px-2 h-[26px] rounded-md text-[12px] border-none cursor-pointer transition-colors truncate max-w-[200px] ${
      on ? 'text-accent bg-accent-subtle' : 'text-muted hover:text-text hover:bg-bg-hover bg-transparent'
    }`
  return (
    <div className="flex flex-col h-full min-h-0">
      <div role="tablist" className="flex items-center gap-1 px-2 h-[34px] shrink-0 border-b border-border">
        <button type="button" role="tab" aria-selected={!current} className={tabCls(!current)} onClick={() => setSelected(null)}>
          {docName}
        </button>
        {tabs.map(t => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={current?.id === t.id}
            className={tabCls(current?.id === t.id)}
            onClick={() => setSelected(t.id)}
          >
            {t.label()}
          </button>
        ))}
      </div>
      <div className={current ? 'hidden' : 'flex flex-col flex-1 min-h-0'}>{children}</div>
      {current && (
        <div className="flex flex-col flex-1 min-h-0 overflow-auto">
          <ErrorBoundary key={current.id} scope={`document-panel-tab-${current.id}`}>
            <current.component target={target} active={active} slot={slot} />
          </ErrorBoundary>
        </div>
      )}
    </div>
  )
}
