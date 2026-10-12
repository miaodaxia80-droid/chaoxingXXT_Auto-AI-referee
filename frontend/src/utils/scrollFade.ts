import type { Directive } from 'vue'

const cleanups = new WeakMap<HTMLElement, () => void>()

function update(el: HTMLElement): void {
  const maxScroll = el.scrollWidth - el.clientWidth
  el.toggleAttribute('data-overflow-start', el.scrollLeft > 1)
  el.toggleAttribute('data-overflow-end', maxScroll - el.scrollLeft > 1)
}

export const vScrollFade: Directive<HTMLElement> = {
  mounted(el) {
    const onScroll = () => update(el)
    const onClick = (event: MouseEvent) => {
      if (!(event.target instanceof Element)) return
      event.target.closest('button')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    }
    const observer = new ResizeObserver(onScroll)
    observer.observe(el)
    el.addEventListener('scroll', onScroll, { passive: true })
    el.addEventListener('click', onClick)
    update(el)
    cleanups.set(el, () => {
      observer.disconnect()
      el.removeEventListener('scroll', onScroll)
      el.removeEventListener('click', onClick)
    })
  },
  updated: update,
  unmounted(el) {
    cleanups.get(el)?.()
    cleanups.delete(el)
  },
}
