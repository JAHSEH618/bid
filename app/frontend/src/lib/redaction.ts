// PR-M6-1 / D3：前端识别脱敏占位符。
// 后端 `services/redaction.py` 在 LLM 调用前替换敏感信息为 `__KIND_xxxxxx__`,
// 章节正文里残留这种 token,前端用本工具找到所有占位符 + banner 提示用户
// 「导出前请手动替换」。
//
// 注意:这里**只显示占位符,不还原**(D3 不可逆)。原值在后端永远不会持久化,
// 用户需要从原始材料里手动核对替换。

export const PLACEHOLDER_RE = /__([A-Z]+)_[0-9a-f]{6}__/g

export interface PlaceholderItem {
  raw: string // 原始占位符,如 `__ORG_a1b2c3__`
  kind: string // 类型前缀,如 `ORG`
  count: number // 在文档内出现次数
}

export const KIND_LABELS: Record<string, string> = {
  ORG: '机构 / 公司',
  PROJ: '项目编号',
  PERSON: '人名',
  PHONE: '电话号码',
  EMAIL: '邮箱',
  IDCARD: '身份证号',
}

export function placeholderLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

/** 扫描文本里所有占位符,按 raw 去重并合计出现次数。 */
export function scanPlaceholders(text: string | null | undefined): PlaceholderItem[] {
  if (!text) return []
  const counts = new Map<string, { kind: string; count: number }>()
  // RegExp /g + matchAll 不会重叠匹配,顺序遍历即可
  for (const m of text.matchAll(PLACEHOLDER_RE)) {
    const raw = m[0]
    const kind = m[1]
    const cur = counts.get(raw)
    if (cur) {
      cur.count += 1
    } else {
      counts.set(raw, { kind, count: 1 })
    }
  }
  return Array.from(counts, ([raw, { kind, count }]) => ({ raw, kind, count }))
}
