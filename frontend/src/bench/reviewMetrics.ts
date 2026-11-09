/** Measure the actual layout without changing any styles in the rendered page. */
export function measureReviewPage(doc: Document) {
  const win = doc.defaultView!
  const root = doc.querySelector<HTMLElement>('[data-scope="body"]')!
  const visible = (element: Element) => {
    const rect = element.getBoundingClientRect()
    return rect.width > 0 && rect.height > 0 && win.getComputedStyle(element).visibility === 'visible'
  }
  const color = (css: string) => {
    const rgb = css.match(/^rgb\((\d+), (\d+), (\d+)\)$/)
    return rgb ? '#' + rgb.slice(1).map((n) => Number(n).toString(16).padStart(2, '0')).join('') : null
  }
  const solidBackground = (element: HTMLElement) => {
    let background: string | null = null
    for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) {
      const style = win.getComputedStyle(parent)
      if (style.opacity !== '1' || style.filter !== 'none' || style.mixBlendMode !== 'normal') return null
      if (background) continue
      if (style.backgroundImage !== 'none' || parent.querySelector(':scope > .wt-container-block__bg-layer')) return null
      background = color(style.backgroundColor)
      if (!background && style.backgroundColor !== 'rgba(0, 0, 0, 0)') return null
    }
    return background
  }
  const pixels = (value: string) => Number.parseFloat(value) || 0
  const nodes = [...root.querySelectorAll<HTMLElement>('[data-wt-node-id]')].filter(visible).map((element) => {
    const style = win.getComputedStyle(element)
    const box = element.getBoundingClientRect()
    const parent = element.parentElement!
    const boundary = parent.getBoundingClientRect()
    const parentStyle = win.getComputedStyle(parent)
    const heading = /^H[1-3]$/.test(element.tagName)
    const plainText = element.matches('.wt-text, .wt-link') && !element.querySelector('*')
    const available = parent.clientWidth - pixels(parentStyle.paddingLeft) - pixels(parentStyle.paddingRight)
    return {
      id: element.dataset.wtNodeId!,
      overflow: box.width > available + 3 && box.right > Math.min(win.innerWidth, boundary.right) + 3,
      clippedHeading: heading && !element.closest('.wt-clamp-wrap') && element.scrollWidth > element.clientWidth + 3,
      fontSize: pixels(style.fontSize),
      foreground: plainText ? color(style.color) : null,
      background: plainText ? solidBackground(element) : null,
      paddingTop: pixels(style.paddingTop),
      paddingBottom: pixels(style.paddingBottom),
      inFrame: box.top < win.innerHeight && box.bottom > 0,
    }
  })
  return {
    nodes,
    sections: [...root.children].filter(visible).slice(0, 8).map((section) =>
      (section as HTMLElement).innerText.trim().replace(/\s+/g, ' ').slice(0, 170)),
    headings: [...root.querySelectorAll<HTMLElement>('h1, h2, h3')].filter(visible)
      .slice(0, 18).map((element) => element.innerText.trim().slice(0, 120)),
    horizontalOverflow: doc.documentElement.scrollWidth > win.innerWidth + 3,
    brokenImages: [...root.querySelectorAll('img')].filter((image) =>
      visible(image) && image.complete && !!image.currentSrc && !image.naturalWidth).length,
  }
}
