import { useEffect, useRef, useState } from 'react'
import { reviewGeneratedDesign } from './api'
import type { DesignReviewResult, GeneratedSite } from './types'

export type DesignReviewState =
  | { status: 'running' }
  | { status: 'finished'; result: DesignReviewResult }

export function useDesignReview() {
  const request = useRef<AbortController | null>(null)
  const [review, setReview] = useState<DesignReviewState | null>(null)
  const [showOriginal, setShowOriginal] = useState(false)
  useEffect(() => () => request.current?.abort(), [])

  function stop() {
    if (!request.current) return
    request.current.abort()
    request.current = null
    setReview(null)
  }

  function reset() {
    stop()
    setReview(null)
    setShowOriginal(false)
  }

  function start(site: GeneratedSite) {
    reset()
    if (!site.pages.length) return
    const controller = new AbortController()
    request.current = controller
    setReview({ status: 'running' })
    reviewGeneratedDesign(site, controller.signal)
      .then((result) => {
        if (request.current !== controller) return
        setReview(result.status === 'disabled' ? null : { status: 'finished', result })
      })
      .catch(() => {
        if (request.current !== controller) return
        setReview({ status: 'finished', result: { status: 'unavailable', pages: [], changes: [], suggestions: [] } })
      })
      .finally(() => {
        if (request.current === controller) request.current = null
      })
  }

  const pages = review?.status === 'finished' && !showOriginal ? review.result.pages : null
  return { review, pages, showOriginal, setShowOriginal, start, stop, reset }
}
