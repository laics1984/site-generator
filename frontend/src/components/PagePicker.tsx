import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { fetchPageRecipe } from '@/lib/api'
import type {
  DetectedBrand,
  IndustryCategory,
  IndustryOption,
  IndustryTemplate,
  PageScaffold,
  SourceContent,
} from '@/lib/types'

interface PagePickerProps {
  source: SourceContent
  industryOverride: IndustryCategory | null
  setIndustryOverride: (value: IndustryCategory | null) => void
  selectedPages: PageScaffold[]
  setSelectedPages: (pages: PageScaffold[]) => void
  setDetectedBrand: (b: DetectedBrand | null) => void
  onConfirm: () => void
  onBack: () => void
  busy: boolean
}

interface TreeNode {
  scaffold: PageScaffold
  children: TreeNode[]
}

/** Convert a flat scaffold list with parent_slug pointers into a tree. */
function buildTree(scaffolds: PageScaffold[]): TreeNode[] {
  const bySlug = new Map<string, TreeNode>()
  const roots: TreeNode[] = []
  for (const s of scaffolds) {
    bySlug.set(s.slug, { scaffold: s, children: [] })
  }
  for (const s of scaffolds) {
    const node = bySlug.get(s.slug)!
    if (s.parent_slug && bySlug.has(s.parent_slug)) {
      bySlug.get(s.parent_slug)!.children.push(node)
    } else {
      roots.push(node)
    }
  }
  roots.sort((a, b) => {
    if (a.scaffold.is_homepage) return -1
    if (b.scaffold.is_homepage) return 1
    return a.scaffold.slug.localeCompare(b.scaffold.slug)
  })
  return roots
}

/** Collect children + grand-children of a node, breadth-first. */
function collectDescendants(node: TreeNode): PageScaffold[] {
  const out: PageScaffold[] = []
  const queue = [...node.children]
  while (queue.length) {
    const n = queue.shift()!
    out.push(n.scaffold)
    queue.push(...n.children)
  }
  return out
}

/** Slugs we auto-expand on first render of a tree.
 *
 *  - any parent whose subtree contains a selected page (so the user can see what's checked)
 *  - any parent with 1-2 children (small subtrees are cheap to show inline)
 *
 *  Big subtrees (≥3 children) stay collapsed by default so the picker doesn't
 *  blow up on sites with lots of sub-pages.
 */
function computeAutoExpansion(
  roots: TreeNode[],
  selectedKeys: Set<string>,
): Set<string> {
  const expanded = new Set<string>()
  const visit = (node: TreeNode): boolean => {
    let descendantSelected = false
    for (const child of node.children) {
      if (visit(child)) descendantSelected = true
    }
    const selfSelected = selectedKeys.has(pageKey(node.scaffold))
    const subtreeSelected = selfSelected || descendantSelected
    if (node.children.length > 0) {
      if (descendantSelected || node.children.length <= 2) {
        expanded.add(node.scaffold.slug)
      }
    }
    return subtreeSelected
  }
  roots.forEach(visit)
  return expanded
}

/** All slugs of nodes that have children — used by Expand-all. */
function allParentSlugs(roots: TreeNode[]): string[] {
  const out: string[] = []
  const visit = (node: TreeNode) => {
    if (node.children.length > 0) {
      out.push(node.scaffold.slug)
      node.children.forEach(visit)
    }
  }
  roots.forEach(visit)
  return out
}

export function PagePicker({
  source,
  industryOverride,
  setIndustryOverride,
  selectedPages,
  setSelectedPages,
  setDetectedBrand,
  onConfirm,
  onBack,
  busy,
}: PagePickerProps) {
  const [inferred, setInferred] = useState<PageScaffold[]>([])
  const [template, setTemplate] = useState<IndustryTemplate | null>(null)
  const [allIndustries, setAllIndustries] = useState<IndustryOption[]>([])
  const [detectedIndustry, setDetectedIndustry] = useState<IndustryCategory | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isFirstLoad = useRef(true)
  const selectedRef = useRef(selectedPages)
  selectedRef.current = selectedPages

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchPageRecipe(source, industryOverride ?? undefined)
      .then((res) => {
        if (cancelled) return
        setInferred(res.inferred_pages)
        setTemplate(res.template)
        setAllIndustries(res.all_industries)
        setDetectedIndustry(res.industry)
        setDetectedBrand(res.detected_brand)

        // Pre-check: all core pages from template + all inferred pages.
        const newDefaults = [...res.template.core_pages, ...res.inferred_pages]
        if (isFirstLoad.current) {
          setSelectedPages(uniqueByKey(newDefaults))
          isFirstLoad.current = false
        } else {
          const prevKeys = new Set(selectedRef.current.map(pageKey))
          const newAll = [
            ...res.template.core_pages,
            ...res.inferred_pages,
            ...res.template.optional_pages,
          ]
          const carried = newAll.filter((p) => prevKeys.has(pageKey(p)))
          const carriedKeys = new Set(carried.map(pageKey))
          const fromDefaults = newDefaults.filter((p) => !carriedKeys.has(pageKey(p)))
          setSelectedPages(uniqueByKey([...carried, ...fromDefaults]))
        }
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Failed to load recipe')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.source_ref, industryOverride])

  const selectedByKey = useMemo(
    () => new Set(selectedPages.map(pageKey)),
    [selectedPages],
  )

  const inferredTree = useMemo(() => buildTree(inferred), [inferred])

  // Expansion state for the inferred tree. Whenever the tree changes (initial
  // load or industry override), recompute the default expansion: parents with
  // a selected descendant or ≤2 children are expanded; bigger subtrees collapse
  // so the picker stays compact on sites with many sub-pages.
  const [expandedSlugs, setExpandedSlugs] = useState<Set<string>>(new Set())
  // Tracks the last tree we computed defaults for, so user toggles aren't
  // overwritten when state re-renders for unrelated reasons.
  const treeFingerprint = useMemo(
    () => inferred.map((p) => p.slug).sort().join("|"),
    [inferred],
  )
  const lastTreeFingerprint = useRef<string>("")
  useEffect(() => {
    if (treeFingerprint === lastTreeFingerprint.current) return
    lastTreeFingerprint.current = treeFingerprint
    setExpandedSlugs(
      computeAutoExpansion(inferredTree, new Set(selectedRef.current.map(pageKey))),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [treeFingerprint])

  function toggleExpand(slug: string) {
    setExpandedSlugs((prev) => {
      const next = new Set(prev)
      if (next.has(slug)) next.delete(slug)
      else next.add(slug)
      return next
    })
  }

  function expandAll() {
    setExpandedSlugs(new Set(allParentSlugs(inferredTree)))
  }

  function collapseAll() {
    setExpandedSlugs(new Set())
  }

  function togglePage(page: PageScaffold) {
    const key = pageKey(page)
    if (selectedByKey.has(key)) {
      setSelectedPages(selectedPages.filter((p) => pageKey(p) !== key))
    } else {
      setSelectedPages([...selectedPages, page])
    }
  }

  function toggleSubtree(node: TreeNode) {
    const nodeKey = pageKey(node.scaffold)
    const descendants = collectDescendants(node)
    const allKeys = [nodeKey, ...descendants.map(pageKey)]
    const allChecked = allKeys.every((k) => selectedByKey.has(k))
    if (allChecked) {
      const toRemove = new Set(allKeys)
      setSelectedPages(
        selectedPages.filter((p) => !toRemove.has(pageKey(p)) || p.is_legal),
      )
    } else {
      const merged = [...selectedPages]
      const have = new Set(merged.map(pageKey))
      for (const s of [node.scaffold, ...descendants]) {
        if (!have.has(pageKey(s))) {
          merged.push(s)
          have.add(pageKey(s))
        }
      }
      setSelectedPages(merged)
    }
  }

  if (loading && inferred.length === 0) return <FirstLoadState onBack={onBack} />
  if (error) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
        {error}
        <button type="button" onClick={onBack} className="mt-2 block text-xs underline">
          Back
        </button>
      </div>
    )
  }
  if (!template) return null

  const subPageCount = inferred.filter((p) => p.parent_slug).length
  const topLevelCount = inferred.filter((p) => !p.parent_slug && !p.is_homepage).length

  return (
    <div className="space-y-4">
      <SourceContextBanner
        source={source}
        discoveredCount={(source.discovered_pages ?? []).length}
      />

      <div className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900">
        <div className="font-semibold">Detected industry: {template.label}</div>
        <div>{template.description}</div>
        {(subPageCount > 0 || topLevelCount > 0) && (
          <div className="mt-2 text-blue-800">
            Discovered <span className="font-semibold">{topLevelCount} sections</span>
            {subPageCount > 0 && (
              <>
                {' '}with{' '}
                <span className="font-semibold">
                  {subPageCount} sub-page{subPageCount === 1 ? '' : 's'}
                </span>
              </>
            )}{' '}
            from the source.
          </div>
        )}
      </div>

      <label className="block">
        <span className="text-sm font-medium text-slate-700">Override industry</span>
        <div className="mt-1 flex items-center gap-2">
          <select
            value={industryOverride ?? detectedIndustry ?? ''}
            disabled={loading}
            onChange={(e) => setIndustryOverride(e.target.value as IndustryCategory)}
            className="block flex-1 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 disabled:bg-slate-100 disabled:text-slate-500"
          >
            {allIndustries.map((ind) => (
              <option key={ind.id} value={ind.id}>
                {ind.label}
              </option>
            ))}
          </select>
          {loading && (
            <span className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-500">
              <Spinner size={12} />
              Updating…
            </span>
          )}
        </div>
      </label>

      {template.core_pages.length > 0 && (
        <PageGroup
          title="Core (always included)"
          description="These pages are always generated. Legal pages use vetted boilerplate."
        >
          {template.core_pages.map((p) => (
            <PageRow
              key={pageKey(p)}
              page={p}
              checked={selectedByKey.has(pageKey(p))}
              onToggle={togglePage}
              locked={!p.is_legal}
              depth={0}
            />
          ))}
        </PageGroup>
      )}

      {inferredTree.length > 0 && (
        <PageGroup
          title="From your source"
          description="Pages inferred from the crawled site. Toggle a section to include or exclude it and its sub-pages."
          actions={
            allParentSlugs(inferredTree).length > 0 && (
              <div className="flex gap-3 text-[11px] font-medium">
                <button
                  type="button"
                  onClick={expandAll}
                  className="text-slate-600 underline hover:text-slate-900"
                >
                  Expand all
                </button>
                <button
                  type="button"
                  onClick={collapseAll}
                  className="text-slate-600 underline hover:text-slate-900"
                >
                  Collapse all
                </button>
              </div>
            )
          }
        >
          {inferredTree.map((node) => (
            <TreeRow
              key={pageKey(node.scaffold)}
              node={node}
              selectedKeys={selectedByKey}
              togglePage={togglePage}
              toggleSubtree={toggleSubtree}
              expandedSlugs={expandedSlugs}
              onToggleExpand={toggleExpand}
              depth={0}
            />
          ))}
        </PageGroup>
      )}

      {template.optional_pages.length > 0 && (
        <PageGroup
          title="Optional"
          description="Pages your source didn't have. Tick to add them."
        >
          {template.optional_pages.map((p) => (
            <PageRow
              key={pageKey(p)}
              page={p}
              checked={selectedByKey.has(pageKey(p))}
              onToggle={togglePage}
              depth={0}
            />
          ))}
        </PageGroup>
      )}

      <div className="flex gap-2 pt-2">
        <button
          type="button"
          disabled={busy || selectedPages.length === 0}
          onClick={onConfirm}
          className="flex-1 rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {busy
            ? 'Generating…'
            : `Generate ${selectedPages.length} page${selectedPages.length === 1 ? '' : 's'}`}
        </button>
        <button
          type="button"
          onClick={onBack}
          disabled={busy}
          className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
        >
          Back
        </button>
      </div>
    </div>
  )
}

// --- tree row ------------------------------------------------------------------

interface TreeRowProps {
  node: TreeNode
  selectedKeys: Set<string>
  togglePage: (p: PageScaffold) => void
  toggleSubtree: (node: TreeNode) => void
  expandedSlugs: Set<string>
  onToggleExpand: (slug: string) => void
  depth: number
}

function TreeRow({
  node,
  selectedKeys,
  togglePage,
  toggleSubtree,
  expandedSlugs,
  onToggleExpand,
  depth,
}: TreeRowProps) {
  const hasChildren = node.children.length > 0
  const expanded = hasChildren && expandedSlugs.has(node.scaffold.slug)
  return (
    <>
      <PageRow
        page={node.scaffold}
        checked={selectedKeys.has(pageKey(node.scaffold))}
        onToggle={hasChildren ? () => toggleSubtree(node) : togglePage}
        depth={depth}
        showSubtreeHint={hasChildren ? node.children.length : undefined}
        expandable={hasChildren}
        expanded={expanded}
        onToggleExpand={
          hasChildren ? () => onToggleExpand(node.scaffold.slug) : undefined
        }
      />
      {hasChildren &&
        expanded &&
        node.children.map((child) => (
          <TreeRow
            key={pageKey(child.scaffold)}
            node={child}
            selectedKeys={selectedKeys}
            togglePage={togglePage}
            toggleSubtree={toggleSubtree}
            expandedSlugs={expandedSlugs}
            onToggleExpand={onToggleExpand}
            depth={depth + 1}
          />
        ))}
    </>
  )
}

interface PageRowProps {
  page: PageScaffold
  checked: boolean
  onToggle: (p: PageScaffold) => void
  locked?: boolean
  depth: number
  showSubtreeHint?: number
  /** Present when this row has children — drives the chevron. */
  expandable?: boolean
  expanded?: boolean
  onToggleExpand?: () => void
}

function PageRow({
  page,
  checked,
  onToggle,
  locked,
  depth,
  showSubtreeHint,
  expandable,
  expanded,
  onToggleExpand,
}: PageRowProps) {
  return (
    <li>
      <label
        className={
          'flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition ' +
          (checked
            ? 'border-blue-200 bg-blue-50'
            : 'border-slate-200 bg-white hover:bg-slate-50') +
          (locked && !page.is_legal ? ' cursor-not-allowed opacity-90' : '')
        }
        style={{ marginLeft: depth * 20 }}
      >
        {expandable && onToggleExpand ? (
          <button
            type="button"
            aria-label={expanded ? 'Collapse sub-pages' : 'Expand sub-pages'}
            onClick={(e) => {
              // Don't bubble into the <label> which would toggle the checkbox.
              e.preventDefault()
              e.stopPropagation()
              onToggleExpand()
            }}
            className="mt-0.5 inline-flex h-4 w-4 items-center justify-center text-slate-500 hover:text-slate-900"
          >
            <svg
              viewBox="0 0 12 12"
              width="10"
              height="10"
              className={
                'transition-transform ' + (expanded ? 'rotate-90' : 'rotate-0')
              }
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="3 1 9 6 3 11" />
            </svg>
          </button>
        ) : (
          // Reserve the same horizontal space so leaf rows align with parents
          <span className="mt-0.5 inline-block h-4 w-4" aria-hidden />
        )}
        <input
          type="checkbox"
          checked={checked}
          disabled={locked && !page.is_legal}
          onChange={() => onToggle(page)}
          className="mt-0.5 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-200"
        />
        <div className="flex-1">
          <div className="flex items-center gap-2">
            {depth > 0 && <span className="text-slate-400">↳</span>}
            <span className="text-sm font-medium text-slate-900">{page.title}</span>
            {page.is_legal && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-800">
                boilerplate
              </span>
            )}
            <span className="text-[11px] text-slate-400">/{page.slug || ''}</span>
            {showSubtreeHint != null && showSubtreeHint > 0 && (
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600">
                {showSubtreeHint} sub-page{showSubtreeHint === 1 ? '' : 's'}
              </span>
            )}
          </div>
          <div className="mt-0.5 text-xs text-slate-600">{page.rationale}</div>
          {page.sections.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {page.sections.map((s, i) => (
                <SectionPill key={`${s}-${i}`} kind={s} />
              ))}
            </div>
          )}
        </div>
      </label>
    </li>
  )
}

// --- group wrapper -------------------------------------------------------------

interface PageGroupProps {
  title: string
  description: string
  children: ReactNode
  /** Optional right-aligned controls shown next to the group title. */
  actions?: ReactNode
}

function PageGroup({ title, description, children, actions }: PageGroupProps) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          {title}
        </div>
        {actions && <div>{actions}</div>}
      </div>
      <p className="mt-0.5 text-xs text-slate-500">{description}</p>
      <ul className="mt-2 space-y-1.5">{children}</ul>
    </div>
  )
}

// --- Source context banner ------------------------------------------------------

function SourceContextBanner({
  source,
  discoveredCount,
}: {
  source: SourceContent
  discoveredCount: number
}) {
  const [expanded, setExpanded] = useState(false)
  const sourceLabel =
    source.source_kind === 'url'
      ? source.source_ref
      : source.source_ref || source.title || 'Pasted document'
  const charCount = (source.raw_text || '').length
  const headingCount = (source.headings || []).length

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-left"
      >
        <div className="min-w-0 flex-1">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            Source
          </div>
          <div className="truncate text-xs font-medium text-slate-800" title={sourceLabel}>
            {sourceLabel}
          </div>
          <div className="text-[11px] text-slate-500">
            {charCount.toLocaleString()} chars · {headingCount} headings
            {discoveredCount > 0 && (
              <>
                {' '}· {discoveredCount} crawled page{discoveredCount === 1 ? '' : 's'}
              </>
            )}
          </div>
        </div>
        <span className="ml-2 text-[11px] font-medium text-slate-600 underline">
          {expanded ? 'Hide' : 'View'}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-slate-200 px-3 py-2">
          {source.title && (
            <div className="mb-2">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                Title
              </div>
              <div className="text-xs text-slate-800">{source.title}</div>
            </div>
          )}
          {(source.discovered_pages ?? []).length > 0 && (
            <div className="mb-2">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                Crawled pages
              </div>
              <ul className="mt-1 max-h-32 space-y-0.5 overflow-auto text-xs text-slate-700">
                {(source.discovered_pages ?? []).map((p, i) => (
                  <li key={i} className="truncate">
                    • {p.url_path || p.source_ref} — {p.title ?? '(untitled)'}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {headingCount > 0 && (
            <div className="mb-2">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                Headings
              </div>
              <ul className="mt-1 max-h-32 space-y-0.5 overflow-auto text-xs text-slate-700">
                {source.headings!.slice(0, 30).map((h, i) => (
                  <li key={i} className="truncate">
                    • {h}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
              Extracted text
            </div>
            <div className="mt-1 max-h-40 overflow-auto rounded border border-slate-200 bg-white p-2 text-xs leading-relaxed text-slate-700 whitespace-pre-wrap">
              {source.raw_text}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// --- First-load skeleton -------------------------------------------------------

function FirstLoadState({ onBack }: { onBack: () => void }) {
  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex items-center gap-3">
          <Spinner />
          <div className="min-w-0">
            <div className="text-sm font-semibold text-slate-900">
              Detecting industry…
            </div>
            <div className="text-xs text-slate-500">
              Analysing extracted content with the local model.
            </div>
          </div>
        </div>
      </div>
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
        <span className="font-semibold">First call can take 20–60s.</span>{' '}
        Ollama is loading the model into memory. Subsequent runs are near-instant.
      </div>
      <div className="rounded-xl border border-slate-200 bg-white p-3">
        <div className="h-3 w-32 animate-pulse rounded bg-slate-200" />
        <div className="mt-2 space-y-1.5">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-12 animate-pulse rounded-xl bg-slate-100"
              style={{ animationDelay: `${i * 120}ms` }}
            />
          ))}
        </div>
      </div>
      <button type="button" onClick={onBack} className="text-xs text-slate-600 underline">
        Back to source
      </button>
    </div>
  )
}

function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg
      className="animate-spin text-blue-600"
      style={{ width: size, height: size }}
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
      <path d="M4 12a8 8 0 018-8" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

// --- helpers -------------------------------------------------------------------

function pageKey(p: PageScaffold): string {
  return `${p.page_type}::${p.slug}`
}

function uniqueByKey(pages: PageScaffold[]): PageScaffold[] {
  const seen = new Set<string>()
  const out: PageScaffold[] = []
  for (const p of pages) {
    const k = pageKey(p)
    if (!seen.has(k)) {
      seen.add(k)
      out.push(p)
    }
  }
  return out
}

const SECTION_DESCRIPTIONS: Record<string, string> = {
  hero: 'Headline + subheadline + primary CTA, optionally over a full-bleed photo.',
  features: '3–6 short benefit cards laid out in a grid.',
  services: 'List of services with descriptions and optional per-service CTAs.',
  testimonials: 'Customer quotes with attribution and avatar photos.',
  about: 'Story/mission heading with body copy and supporting image.',
  faq: 'Accordion-style question + answer pairs.',
  cta: 'Conversion-focused section with photo background + dark overlay and a single action.',
  contact: 'Heading, contact details (email/phone), and a contact form.',
  pricing: 'Tiered pricing cards with feature lists; one tier highlighted.',
  team: 'Team member cards with photos, names, roles, and bios.',
  gallery: 'Responsive image grid for visual storytelling.',
  menu: 'Categorised menu items with names, descriptions, and prices.',
  process: 'Numbered steps explaining how you work.',
}

function SectionPill({ kind }: { kind: string }) {
  const description = SECTION_DESCRIPTIONS[kind] ?? `Section: ${kind}`
  return (
    <span
      title={description}
      className="inline-flex items-center rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] font-medium text-slate-600"
    >
      {kind}
    </span>
  )
}
