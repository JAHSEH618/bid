import { useEffect, useRef } from 'react'
import { isMockEnabled, MockProjectEventSource } from '@/lib/mock'

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
