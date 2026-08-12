import type { EventLevel, SystemEvent } from '@/api/types'

const API_PREFIX = '/api/v1'
const INITIAL_RETRY_DELAY_MS = 1_000
const MAX_RETRY_DELAY_MS = 15_000

export type EventStreamStatus = 'connecting' | 'connected' | 'offline'

interface StreamSystemEventsOptions {
  afterId: number
  level: EventLevel | null
  signal: AbortSignal
  onEvent: (event: SystemEvent) => void
  onStatus: (status: EventStreamStatus) => void
}

class EventStreamHttpError extends Error {
  constructor(readonly status: number) {
    super(`Event stream request failed (${status})`)
  }
}

function isSystemEvent(value: unknown): value is SystemEvent {
  if (!value || typeof value !== 'object') return false
  const event = value as Partial<SystemEvent>
  return (
    typeof event.id === 'number' &&
    typeof event.kind === 'string' &&
    (event.level === 'info' || event.level === 'warning' || event.level === 'error') &&
    typeof event.occurred_at === 'string'
  )
}

function parseFrame(frame: string): { id: number | null; data: string } | null {
  let id: number | null = null
  const data: string[] = []

  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue
    const separator = line.indexOf(':')
    const field = separator === -1 ? line : line.slice(0, separator)
    let value = separator === -1 ? '' : line.slice(separator + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'id' && /^\d+$/.test(value)) id = Number(value)
    if (field === 'data') data.push(value)
  }

  return data.length ? { id, data: data.join('\n') } : null
}

async function consumeEventStream(
  body: ReadableStream<Uint8Array>,
  signal: AbortSignal,
  onFrame: (id: number | null, data: string) => void,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })

      let separator = /\r?\n\r?\n/.exec(buffer)
      while (separator) {
        const frame = buffer.slice(0, separator.index)
        buffer = buffer.slice(separator.index + separator[0].length)
        const parsed = parseFrame(frame)
        if (parsed) onFrame(parsed.id, parsed.data)
        separator = /\r?\n\r?\n/.exec(buffer)
      }

      if (done) return
    }
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}

function waitForRetry(delay: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve()
      return
    }
    const timeout = window.setTimeout(done, delay)
    signal.addEventListener('abort', done, { once: true })

    function done() {
      window.clearTimeout(timeout)
      signal.removeEventListener('abort', done)
      resolve()
    }
  })
}

export async function streamSystemEvents(options: StreamSystemEventsOptions): Promise<void> {
  let cursor = Math.max(0, options.afterId)
  let retryDelay = INITIAL_RETRY_DELAY_MS

  while (!options.signal.aborted) {
    options.onStatus('connecting')
    try {
      const params = new URLSearchParams({ after_id: String(cursor) })
      if (options.level) params.set('level', options.level)
      const response = await fetch(`${API_PREFIX}/events/stream?${params.toString()}`, {
        headers: {
          Accept: 'text/event-stream',
          'Last-Event-ID': String(cursor),
        },
        credentials: 'include',
        cache: 'no-store',
        signal: options.signal,
      })
      if (!response.ok) throw new EventStreamHttpError(response.status)
      if (!response.body) throw new Error('Event stream response has no body')

      options.onStatus('connected')
      retryDelay = INITIAL_RETRY_DELAY_MS
      await consumeEventStream(response.body, options.signal, (frameId, data) => {
        try {
          const event: unknown = JSON.parse(data)
          if (!isSystemEvent(event)) return
          cursor = Math.max(cursor, frameId ?? event.id, event.id)
          options.onEvent(event)
        } catch {
          // Ignore a malformed frame and keep the live connection usable.
        }
      })
    } catch (error) {
      if (options.signal.aborted) return
      options.onStatus('offline')
      if (error instanceof EventStreamHttpError && [401, 403].includes(error.status)) return
    }

    await waitForRetry(retryDelay, options.signal)
    retryDelay = Math.min(retryDelay * 2, MAX_RETRY_DELAY_MS)
  }
}
