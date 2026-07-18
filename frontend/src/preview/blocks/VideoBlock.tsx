/**
 * PORT of webtree-public/components/blocks/VideoBlock.vue — keep in lockstep.
 *
 * Upstream shows a YouTube thumbnail facade and swaps in the real player
 * iframe on click; Vimeo/other embeds render the iframe directly. Both are
 * ported, so the block occupies the same 16/9 frame the published page gives it.
 */
import { useState } from 'react'
import type { CSSProperties } from 'react'
import type { PublicBlockNode } from '../lib/public'
import { getNodeClasses, getNodeStyles, getStringField } from '../lib/blockRuntime'
import { getNodeDomId } from '../lib/responsiveRuntime'
import { parseVideoEmbed } from '../lib/videoEmbed'

export function VideoBlock({ node }: { node: PublicBlockNode }) {
  const nodeClasses = getNodeClasses(node)
  const nodeStyles = getNodeStyles(node) as CSSProperties
  const nodeDomId = getNodeDomId(node) || undefined
  const title = getStringField(node, 'title') || 'Embedded video'
  const embed = parseVideoEmbed(getStringField(node, 'src'))

  const [activated, setActivated] = useState(false)
  const useFacade = Boolean(embed?.thumbnailUrl) && !activated

  if (!embed) return null

  const iframeSrc =
    activated && embed.provider === 'youtube' ? `${embed.embedUrl}?autoplay=1` : embed.embedUrl

  return (
    <div
      className={['wt-video-block', nodeClasses].filter(Boolean).join(' ')}
      style={nodeStyles}
      data-wt-node-id={nodeDomId}
    >
      <div className="wt-video-block__frame">
        {useFacade ? (
          <button
            type="button"
            className="wt-video-block__facade"
            style={{ backgroundImage: `url('${embed.thumbnailUrl}')` }}
            aria-label={`Play video: ${title}`}
            onClick={() => setActivated(true)}
          >
            <span className="wt-video-block__play" aria-hidden="true">
              <svg viewBox="0 0 68 48" width="68" height="48">
                <path
                  className="wt-video-block__play-bg"
                  d="M66.52 7.74c-.78-2.93-2.49-5.41-5.42-6.19C55.79.13 34 0 34 0S12.21.13 6.9 1.55c-2.93.78-4.63 3.26-5.42 6.19C.06 13.05 0 24 0 24s.06 10.95 1.48 16.26c.78 2.93 2.49 5.41 5.42 6.19C12.21 47.87 34 48 34 48s21.79-.13 27.1-1.55c2.93-.78 4.64-3.26 5.42-6.19C67.94 34.95 68 24 68 24s-.06-10.95-1.48-16.26z"
                />
                <path d="M45 24 27 14v20z" fill="#fff" />
              </svg>
            </span>
          </button>
        ) : (
          <iframe
            className="wt-video-block__iframe"
            src={iframeSrc}
            title={title}
            loading="lazy"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowFullScreen
          />
        )}
      </div>
    </div>
  )
}
