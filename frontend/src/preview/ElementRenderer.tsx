/**
 * PORT of webtree-public/components/renderer/ElementRenderer.vue.
 * Keep the registry in lockstep: a type missing here renders as its bare
 * children in the preview while the published page renders a real block.
 */
import type { PublicBlockNode } from './lib/public'
import { getNodeChildren, getNodeKey, normalizeBlockType } from './lib/schema'
import { ContainerBlock } from './blocks/ContainerBlock'
import { SectionBlock } from './blocks/SectionBlock'
import { TextBlock } from './blocks/TextBlock'
import { ImageBlock } from './blocks/ImageBlock'
import { LinkBlock } from './blocks/LinkBlock'
import { MenuBlock } from './blocks/MenuBlock'
import { HeroBlock } from './blocks/HeroBlock'
import { VideoBlock } from './blocks/VideoBlock'
import { ContactFormBlock } from './blocks/ContactFormBlock'
import { CmsListBlock } from './blocks/CmsListBlock'
import { DynamicFieldBlock } from './blocks/DynamicFieldBlock'

type BlockComponent = (props: { node: PublicBlockNode }) => JSX.Element | null

// Mirrors the Vue registry's keys exactly — note they are `normalizeBlockType`
// output, so `2Col` from the generator arrives here as `2col`.
const registry: Record<string, BlockComponent> = {
  header: ContainerBlock,
  body: ContainerBlock,
  footer: ContainerBlock,
  container: ContainerBlock,
  '2col': ContainerBlock,
  '3col': ContainerBlock,
  text: TextBlock,
  section: SectionBlock,
  image: ImageBlock,
  video: VideoBlock,
  link: LinkBlock,
  menu: MenuBlock,
  hero: HeroBlock,
  contactform: ContactFormBlock,
  articleslist: CmsListBlock,
  eventslist: CmsListBlock,
  cmsarchiveheader: DynamicFieldBlock,
  articletitle: DynamicFieldBlock,
  articlebody: DynamicFieldBlock,
  articleimage: DynamicFieldBlock,
  articleexcerpt: DynamicFieldBlock,
  articledate: DynamicFieldBlock,
  articleauthor: DynamicFieldBlock,
  articlecategory: DynamicFieldBlock,
  articletag: DynamicFieldBlock,
  archivetitle: DynamicFieldBlock,
  archivedescription: DynamicFieldBlock,
  eventtitle: DynamicFieldBlock,
  eventbody: DynamicFieldBlock,
  eventimage: DynamicFieldBlock,
  eventexcerpt: DynamicFieldBlock,
  eventdate: DynamicFieldBlock,
  eventlocation: DynamicFieldBlock,
}

export function ElementRenderer({ node }: { node: PublicBlockNode }) {
  const Component = registry[normalizeBlockType(node?.type)]
  if (Component) {
    return <Component node={node} />
  }

  // Unknown type: fall through to its children rather than dropping the
  // subtree, same as upstream's `wt-unknown-block { display: contents }`.
  const children = getNodeChildren(node)
  if (children.length === 0) {
    return null
  }
  return (
    <div className="wt-unknown-block" data-unsupported-block="true">
      {children.map((child, index) => (
        <ElementRenderer key={getNodeKey(child, index)} node={child} />
      ))}
    </div>
  )
}

/** Shared by every block that renders a node's children. */
export function renderChildren(children: PublicBlockNode[]) {
  return children.map((child, index) => (
    <ElementRenderer key={getNodeKey(child, index)} node={child} />
  ))
}
