import { useEffect, useRef } from 'react'
import { isMockEnabled } from '@/lib/mock-flag'

// SSE 事件类型对齐 backend workflow/sync.py:publish_event 实际调用站点
// (extract_documents.py / generate_outline.py / outline_review.py /
//  human_review.py / pick_chapter.py / write_chapter.py / gen_visuals.py /
//  update_state.py / assemble.py)。
//
// 服务端帧:
//   · `event: ready` 紧跟订阅成功(订阅前用 addEventListener 才能听到)
//   · `data: {...}\n\n` 业务事件:type 字段 ∈ 下列枚举
//   · `: ping\n\n` 每 20s 心跳,浏览器自动忽略
export type ProjectEventType =
  // 文档抽取(extract_documents.py)
  | 'extract_documents_passthrough'
  | 'extract_documents_done'
  // 提纲生成
  | 'outline_started'
  | 'outline_ready'
  // 章节循环
  | 'chapter_started'
  | 'chapter_picked'
  | 'chapter_ready_to_generate'
  | 'chapter_prefetched'
  | 'chapter_token'
  | 'chapter_visuals_ready'
  | 'awaiting_review'
  | 'chapter_failed'
  | 'chapter_approved'
  | 'chapter_skipped'
  | 'chapter_max_retry_skip'
  // 收尾
  | 'proposal_ready'
  | 'error'

export interface ProjectEvent {
  type: ProjectEventType
  chapter_index?: number
  chapter_title?: string
  delta?: string
  chapter_text?: string
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
    let es: { close: () => void } | null = null
    let cancelled = false

    const wireListeners = (
      target: EventSource | { onmessage: ((e: MessageEvent) => void) | null;
        onerror: ((e: Event) => void) | null;
        onopen: ((e: Event) => void) | null },
    ) => {
      target.onopen = () => {
        optionsRef.current.onOpen?.()
      }
      target.onmessage = (msg: MessageEvent) => {
        // 心跳 `: ping` 不会触发 onmessage,这里只兜底空 data。
        if (!msg.data) return
        try {
          const parsed = JSON.parse(msg.data) as ProjectEvent
          handlerRef.current(parsed)
        } catch (err) {
          console.warn('[useProjectStream] parse failed', err, msg.data)
        }
      }
      target.onerror = (err: Event) => {
        // EventSource 自带重连(默认 ~3s),不主动 close。
        optionsRef.current.onError?.(err)
      }
    }

    if (isMockEnabled()) {
      // 动态 import:prod build 时不可达,fixtures 不进 bundle(REVIEW-3 🟡 #4)
      void import('@/lib/mock').then(({ MockProjectEventSource }) => {
        if (cancelled) return
        const mock = new MockProjectEventSource(url)
        es = mock
        wireListeners(mock)
      })
    } else {
      const real = new EventSource(url, { withCredentials: true })
      es = real
      wireListeners(real)
    }

    return () => {
      cancelled = true
      es?.close()
    }
    // 仅用 projectId / enabled 触发重连,避免 onEvent 引用变化每次重订阅。
    // (react-hooks 5.x 已识别 onEventRef pattern,不再报 exhaustive-deps,
    //  历史的 disable 注释移除以兼容 --report-unused-disable-directives)
  }, [projectId, options.enabled])
}
