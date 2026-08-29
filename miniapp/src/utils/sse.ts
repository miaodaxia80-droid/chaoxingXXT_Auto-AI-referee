import { API_BASE } from '@/config'
import type { SystemEvent } from '@/api/types'

/**
 * 事件流客户端（双平台）：
 * - 微信小程序：uni.request + enableChunked，onChunkReceived 增量解析 SSE 帧
 * - H5：fetch + ReadableStream 手动解析 SSE 帧
 *
 * 两平台共用帧解析与重连逻辑：断线后指数退避（1s→15s），
 * 以最后收到的事件 id 作为 after_id 续传；收到 401/403 时永久停止。
 */

export interface SseHandlers {
  onEvent(event: SystemEvent): void
  onState?(state: 'connecting' | 'connected' | 'offline'): void
  onAuthFailed?(): void
}

function isSystemEvent(value: unknown): value is SystemEvent {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.id === 'number' &&
    typeof record.kind === 'string' &&
    (record.level === 'info' || record.level === 'warning' || record.level === 'error') &&
    typeof record.occurred_at === 'string'
  )
}

/** 解析单帧：返回 {id, kind, payload}；心跳注释帧返回 null。 */
function parseFrame(frame: string): { id: number; payload: unknown } | null {
  let id = 0
  let sawId = false
  const dataLines: string[] = []
  for (const line of frame.replace(/\r\n/g, '\n').split('\n')) {
    if (line.startsWith(':')) continue
    if (line.startsWith('id:')) {
      const parsed = Number(line.slice(3).trim())
      if (Number.isFinite(parsed)) {
        id = parsed
        sawId = true
      }
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim())
    }
  }
  if (!dataLines.length) return null
  let payload: unknown = null
  try {
    payload = JSON.parse(dataLines.join('\n'))
  } catch {
    return null
  }
  return { id: sawId ? id : 0, payload }
}

/** 增量 UTF-8 帧切分器：喂入 ArrayBuffer，产出完整帧文本。 */
function createFrameSplitter(onFrame: (frame: string) => void): (chunk: ArrayBuffer) => void {
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  return (chunk: ArrayBuffer) => {
    buffer += decoder.decode(chunk, { stream: true })
    let index: number
    while ((index = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      if (frame.trim()) onFrame(frame)
    }
  }
}

/** 重连调度器：记录游标、退避重连、401/403 永久停止。 */
function createStreamController(
  handlers: SseHandlers,
  connect: () => void,
): {
  markConnected(): void
  feedFrame(frame: string): void
  onEnd(statusCode: number): void
  stop(): void
  lastId(): number
} {
  let stopped = false
  let cursor = 0
  let retryMs = 1000
  let retryTimer: ReturnType<typeof setTimeout> | null = null

  const scheduleReconnect = (statusCode: number) => {
    if (stopped) return
    if (statusCode === 401 || statusCode === 403) {
      stopped = true
      handlers.onState?.('offline')
      handlers.onAuthFailed?.()
      return
    }
    handlers.onState?.('offline')
    retryTimer = setTimeout(() => {
      if (stopped) return
      handlers.onState?.('connecting')
      connect()
    }, retryMs)
    retryMs = Math.min(retryMs * 2, 15_000)
  }

  return {
    markConnected() {
      handlers.onState?.('connected')
    },
    feedFrame(frame: string) {
      const parsed = parseFrame(frame)
      if (parsed === null) return
      if (parsed.id > 0) cursor = parsed.id
      retryMs = 1000
      if (isSystemEvent(parsed.payload)) {
        handlers.onEvent(parsed.payload)
      }
    },
    onEnd(statusCode: number) {
      if (stopped) return
      scheduleReconnect(statusCode)
    },
    stop() {
      stopped = true
      if (retryTimer) clearTimeout(retryTimer)
    },
    lastId: () => cursor,
  }
}

// #ifdef MP-WEIXIN
function startMpStream(handlers: SseHandlers): () => void {
  let connect = () => {}
  const controller = createStreamController(handlers, () => connect())
  const feed = createFrameSplitter((frame) => controller.feedFrame(frame))
  let currentTask: UniApp.RequestTask | null = null

  connect = () => {
    handlers.onState?.('connecting')
    const header: Record<string, string> = { Accept: 'text/event-stream' }
    const sessionCookie = uni.getStorageSync('cx.session_cookie') as string
    if (sessionCookie) header['Cookie'] = sessionCookie
    currentTask = uni.request({
      url: `${API_BASE}/api/v1/events/stream?after_id=${controller.lastId()}&follow=true`,
      method: 'GET',
      header,
      enableChunked: true,
      success: (res) => {
        currentTask = null
        controller.onEnd(res.statusCode)
      },
      fail: () => {
        currentTask = null
        controller.onEnd(0)
      },
    })
    const task = currentTask as unknown as {
      onChunkReceived?: (callback: (res: { data: ArrayBuffer }) => void) => void
    }
    let firstChunk = true
    task.onChunkReceived?.((res) => {
      if (firstChunk) {
        firstChunk = false
        controller.markConnected()
      }
      feed(res.data)
    })
  }

  connect()
  return () => {
    controller.stop()
    currentTask?.abort()
  }
}
// #endif

// #ifdef H5
function startH5Stream(handlers: SseHandlers): () => void {
  let connect = () => {}
  const controller = createStreamController(handlers, () => connect())
  let xhr: XMLHttpRequest | null = null

  connect = () => {
    handlers.onState?.('connecting')
    const request = new XMLHttpRequest()
    xhr = request
    const url = `${API_BASE}/api/v1/events/stream?after_id=${controller.lastId()}&follow=true`
    let buffer = ''
    let consumed = 0
    let firstProgress = false
    request.open('GET', url, true)
    request.setRequestHeader('Accept', 'text/event-stream')
    request.onprogress = () => {
      if (!firstProgress) {
        firstProgress = true
        controller.markConnected()
      }
      // responseText 随流累积，按已消费长度取增量，避免重复解析
      const text = request.responseText
      buffer += text.slice(consumed)
      consumed = text.length
      let index: number
      while ((index = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, index)
        buffer = buffer.slice(index + 2)
        if (frame.trim()) controller.feedFrame(frame)
      }
    }
    request.onload = () => {
      controller.onEnd(request.status)
    }
    request.onerror = () => {
      controller.onEnd(0)
    }
    request.send()
  }

  connect()
  return () => {
    controller.stop()
    xhr?.abort()
  }
}
// #endif

export function startEventStream(handlers: SseHandlers): () => void {
  // #ifdef MP-WEIXIN
  return startMpStream(handlers)
  // #endif

  // #ifdef H5
  return startH5Stream(handlers)
  // #endif

  // #ifndef MP-WEIXIN || H5
  handlers.onState?.('offline')
  return () => {}
  // #endif
}
