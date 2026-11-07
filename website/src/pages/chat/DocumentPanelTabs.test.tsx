/**
 * Edition tabs on an opened file or artifact in the chat side panel: a component
 * registered with `registerDocumentPanelTabs` shows as a tab beside the document,
 * gets the document it sits on, follows its app's enable state, and a tab that
 * throws is isolated while the document stays usable.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { DocumentPanelTabs } from './DocumentPanelTabs'
import {
  registerDocumentPanelTabs,
  resetDocumentPanelTabsForTests,
  type DocumentPanelTabProps,
  type DocumentPanelTarget,
} from './documentPanelTabs'

const seen: DocumentPanelTabProps[] = []

function SampleTab(props: DocumentPanelTabProps) {
  seen.push(props)
  const t = props.target
  return <div>sample body for {t.kind === 'file' ? t.path : t.slug}</div>
}

function BrokenTab(): never {
  throw new Error('tab failed')
}

function mount(target: DocumentPanelTarget, apps: unknown[] = []) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  qc.setQueryData(['apps'], apps)
  render(
    <QueryClientProvider client={qc}>
      <DocumentPanelTabs target={target} active slot="s1">
        <div>document body</div>
      </DocumentPanelTabs>
    </QueryClientProvider>,
  )
}

const fileTarget: DocumentPanelTarget = { kind: 'file', path: '/w/notes/plan.md' }

beforeEach(() => {
  seen.length = 0
  resetDocumentPanelTabsForTests()
})

afterEach(() => {
  vi.restoreAllMocks()
  resetDocumentPanelTabsForTests()
})

describe('DocumentPanelTabs', () => {
  it('renders the document alone, with no tab strip, when nothing is registered', () => {
    mount(fileTarget)
    expect(screen.getByText('document body')).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).toBeNull()
  })

  it('shows no tab while the app the tab requires is disabled', () => {
    registerDocumentPanelTabs([
      { id: 'sample', label: () => 'Sample', requiresApp: 'sample-app', component: SampleTab },
    ])
    mount(fileTarget, [{ name: 'sample-app', enabled: false }])
    expect(screen.getByText('document body')).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).toBeNull()
  })

  it('shows the tab while its app is enabled and passes it the opened document', () => {
    registerDocumentPanelTabs([
      { id: 'sample', label: () => 'Sample', requiresApp: 'sample-app', component: SampleTab },
    ])
    mount(fileTarget, [{ name: 'sample-app', enabled: true }])
    expect(screen.getByRole('tab', { name: 'plan.md' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('tab', { name: 'Sample' }))
    expect(screen.getByRole('tab', { name: 'Sample' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('sample body for /w/notes/plan.md')).toBeInTheDocument()
    expect(seen.at(-1)).toMatchObject({ target: fileTarget, active: true, slot: 's1' })
    // The document stays mounted (hidden), so an unsaved buffer survives the switch.
    expect(screen.getByText('document body')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'plan.md' }))
    expect(screen.queryByText('sample body for /w/notes/plan.md')).toBeNull()
  })

  it('names an artifact by its slug', () => {
    registerDocumentPanelTabs([{ id: 'sample', label: () => 'Sample', component: SampleTab }])
    mount({ kind: 'artifact', slug: 'weekly-report', artifactKind: 'markdown' })
    fireEvent.click(screen.getByRole('tab', { name: 'Sample' }))
    expect(screen.getByText('sample body for weekly-report')).toBeInTheDocument()
  })

  it('shows the crash card for a tab that throws and keeps the document', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    registerDocumentPanelTabs([{ id: 'broken', label: () => 'Broken', component: BrokenTab }])
    mount(fileTarget)
    fireEvent.click(screen.getByRole('tab', { name: 'Broken' }))
    expect(screen.getByText('tab failed')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'plan.md' }))
    expect(screen.getByText('document body')).toBeVisible()
  })

  it('treats a duplicate id as a seam collision', () => {
    registerDocumentPanelTabs([{ id: 'sample', label: () => 'Sample', component: SampleTab }])
    expect(() =>
      registerDocumentPanelTabs([{ id: 'sample', label: () => 'Again', component: SampleTab }]),
    ).toThrow(/sample/)
  })

  it('is mounted around file and artifact tab bodies', () => {
    const src = readFileSync(resolve(__dirname, 'SidePanelTabBodies.tsx'), 'utf8')
    expect(src).toContain("<DocumentPanelTabs target={{ kind: 'file'")
    expect(src).toContain("<DocumentPanelTabs target={{ kind: 'artifact'")
  })
})
