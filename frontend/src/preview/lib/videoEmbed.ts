// Vendored from webtree-public/lib/videoEmbed.ts — verbatim, no local
// imports to change.
//
// Parses a stored video reference (the builder writes a canonical embed URL into
// `content.src`, but this also tolerates legacy watch/short URLs and pasted
// <iframe> snippets) into a clean embed URL plus lite-embed metadata.
//
// Mirror of builder/src/lib/video-embed.ts → keep the two in lockstep.

export type VideoProvider = 'youtube' | 'vimeo' | 'other'

export interface VideoEmbed {
  provider: VideoProvider
  videoId: string | null
  embedUrl: string
  thumbnailUrl: string | null
}

const YOUTUBE_ID = '[A-Za-z0-9_-]{6,}'

const YOUTUBE_PATTERNS = [
  new RegExp(`youtu\\.be/(${YOUTUBE_ID})`, 'i'),
  new RegExp(`youtube(?:-nocookie)?\\.com/embed/(${YOUTUBE_ID})`, 'i'),
  new RegExp(`youtube\\.com/shorts/(${YOUTUBE_ID})`, 'i'),
  new RegExp(`youtube\\.com/watch\\?(?:.*&)?v=(${YOUTUBE_ID})`, 'i'),
  new RegExp(`youtube\\.com/v/(${YOUTUBE_ID})`, 'i'),
]

const VIMEO_PATTERN = /vimeo\.com\/(?:video\/)?(\d+)/i

const youtubeEmbed = (id: string): VideoEmbed => ({
  provider: 'youtube',
  videoId: id,
  embedUrl: `https://www.youtube.com/embed/${id}`,
  thumbnailUrl: `https://i.ytimg.com/vi/${id}/hqdefault.jpg`,
})

const vimeoEmbed = (id: string): VideoEmbed => ({
  provider: 'vimeo',
  videoId: id,
  embedUrl: `https://player.vimeo.com/video/${id}`,
  thumbnailUrl: null,
})

const extractUrl = (input: string): string => {
  const iframeMatch = input.match(/<iframe[^>]*\ssrc=["']([^"']+)["']/i)
  return (iframeMatch ? iframeMatch[1] : input).trim()
}

export const parseVideoEmbed = (input: string | null | undefined): VideoEmbed | null => {
  if (!input) return null
  const url = extractUrl(input)
  if (!url) return null

  for (const pattern of YOUTUBE_PATTERNS) {
    const match = url.match(pattern)
    if (match) return youtubeEmbed(match[1])
  }

  const vimeoMatch = url.match(VIMEO_PATTERN)
  if (vimeoMatch) return vimeoEmbed(vimeoMatch[1])

  if (/^https?:\/\//i.test(url)) {
    return { provider: 'other', videoId: null, embedUrl: url, thumbnailUrl: null }
  }

  return null
}
