import { useEffect, useRef } from 'react'
import { isMockEnabled, MockProjectEventSource } from '@/lib/mock'

// SSE 事件类型对齐 IMPLEMENTATION_SPEC §16.3 + REQUIREMENTS FR-3.5。
// 字段细节(payload schema)在 #11 后端 stream 实现完成后再补,这里只锁顶层结构。
export type ProjectEventType =
  | 'chapter_started'
  | 'chapter_token'
  | 'chapter_ready'
  | 'awaiting_review'
  | 'chapter_failed'
  | 'chapter_approved'
  | 'chapter_skipped'
  | 'outline_ready'
  | 'proposal_ready'
  | 'error'
  | 'ping'

export interface ProjectEvent {
  type: ProjectEventType
  chapter_index?: number
  delta?: string
  payload?: unknown
}

export interface UseProjectStreamOptions {
  enabled?: boolean
  onOpen?: () => void
  onError?: (e: Event) => void
}

export function useProjectStream(
  projectId: number | null,
  onEvent: (e: ProjectEvent) => void,
  options: UseProjectStreamOptions = {},
) {
  const handlerRef = useRef(onEvent)
  handlerRef.current = onEvent
  const optionsRef = useRef(options)
  optionsRef.current = options

  useEffect(() => {
    if (projectId == null) return
    if (options.enabled === false) return

    const url = `/api/projects/${projectId}/stream`
    const es: EventSource | MockProjectEventSource = isMockEnabled()
      ? new MockProjectEventSource(url)
      : new EventSource(url, { withCredentials: true })

    es.onopen = () => {
      optionsRef.current.onOpen?.()
    }

    es.onmessage = (msg) => {
      // 后端心跳是 SSE 注释行(`: ping`),浏览器不会触发 onmessage,这里忽略 data 为空的边界情况。
      if (!msg.data) return
      try {
        const parsed = JSON.parse(msg.data) as ProjectEvent
        handlerRef.current(parsed)
      } catch (err) {
        console.warn('[useProjectStream] parse failed', err, msg.data)
      }
    }

    es.onerror = (err) => {
      // EventSource 自带重连(浏览器默认 ~3s),一般不要主动 close;
      // 但如果状态进入 CLOSED(后端真的关闭),fall through 让 cleanup 收尾。
      optionsRef.current.onError?.(err)
    }

    return () => {
      es.close()
    }
    // 仅用 projectId / enabled 触发重连,避免 onEvent 引用变化每次重订阅。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, options.enabled])
}
