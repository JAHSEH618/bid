import { Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  useBlackboardEntities,
  type BlackboardEntryDTO,
} from '@/api/projects'
import { readApiError } from '@/lib/apiFetch'

// Phase 1C (2026-05-16):10 桶实体黑板的折叠展示面板。
// 数据由 categorize_blackboard 节点在 material_understanding pass / skip
// 之后写入 Project.blackboard_entities。后端 GET 端点 NULL 时回 404,
// useBlackboardEntities 在「暂未就绪」时 3s 轮询。
//
// 桶顺序固定,与后端 ENTITY_BUCKETS 一致;label_zh / desc 跟后端
// _BUCKET_LABELS_ZH 对齐(改这里也要回头改后端,否则用户看到的 label
// 跟 LLM-1/2 prompt 里的对不上)。

interface BucketMeta {
  key: string
  label: string
  desc: string
}

const BUCKETS: BucketMeta[] = [
  {
    key: 'project_info',
    label: '项目背景信息',
    desc: '项目概况 / 招标方 / 招标项目本身',
  },
  {
    key: 'company_info',
    label: '公司 / 组织信息',
    desc: '招标方组织架构 / 投标方公司层面要求',
  },
  {
    key: 'personnel_info',
    label: '人员资质要求',
    desc: '项目经理 / 关键岗位资质 / 人数 / 经验',
  },
  {
    key: 'scoring_rules',
    label: '评分细则与权重',
    desc: '投标核心导向,LLM-1/2 优先按此组织内容',
  },
  {
    key: 'technical_requirements',
    label: '技术要求 / SLA / 参数',
    desc: '可用性 / 响应 / 接口 / 性能指标',
  },
  {
    key: 'qualification_requirements',
    label: '投标资质 / 业绩门槛',
    desc: '证书 / 类似项目业绩 / 硬性资格',
  },
  {
    key: 'timeline_constraints',
    label: '工期 / 时间节点',
    desc: '截止时间 / 阶段划分 / 里程碑',
  },
  {
    key: 'commercial_terms',
    label: '商务条款 / 报价规则',
    desc: '付款条件 / 价格上下限 / 商务模型',
  },
  {
    key: 'compliance_constraints',
    label: '法律 / 合规 / 强制条款',
    desc: '法规 / 合同必须项 / 政策约束',
  },
  {
    key: 'risk_signals',
    label: '风险信号 / 一票否决项',
    desc: '容易踩雷 / 隐含风险 / 漏写直接出局',
  },
]

interface Props {
  projectId: number
}

export function BlackboardEntitiesPanel({ projectId }: Props) {
  const q = useBlackboardEntities(projectId)
  const data = q.data?.blackboard_entities

  if (q.isLoading && !data) {
    return (
      <Card>
        <CardHeader>
          <p className="text-meta text-mute">Blackboard · 10 buckets</p>
          <CardTitle>项目实体黑板</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3 text-sm text-mute">
            <Loader2 className="h-4 w-4 animate-spin" />
            LLM 正在拆 10 个实体桶…
          </div>
        </CardContent>
      </Card>
    )
  }

  if (!data) {
    // 区分 404(暂未就绪)和真错误(401/500),给不同文案
    const errMsg = q.error ? readApiError(q.error, '加载失败') : ''
    const stillCooking =
      !q.error || errMsg.includes('404') || errMsg.includes('暂未就绪')
    return (
      <Card>
        <CardHeader>
          <p className="text-meta text-mute">Blackboard · 10 buckets</p>
          <CardTitle>项目实体黑板</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-mute">
            {stillCooking
              ? '实体桶尚未就绪 — categorize_blackboard 节点要等材料理解 pass / skip 之后跑。本页会自动刷新。'
              : `加载失败:${errMsg}`}
          </p>
        </CardContent>
      </Card>
    )
  }

  const totalEntries = Object.values(data).reduce<number>(
    (acc, items) =>
      acc + (Array.isArray(items) ? items.length : 0),
    0,
  )

  return (
    <Card>
      <CardHeader>
        <p className="text-meta text-mute">Blackboard · 10 buckets</p>
        <CardTitle>
          项目实体黑板
          <span className="ml-3 text-meta text-mute font-normal">
            共 {totalEntries} 条
          </span>
        </CardTitle>
        <p className="mt-2 text-sm text-mute">
          LLM 把招标材料拆成 10 个实体桶,后续 LLM-1 / LLM-2 会按章节相关性
          挑桶塞进 prompt。点击桶名展开查看具体条目。
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {BUCKETS.map((bucket) => {
          const items: BlackboardEntryDTO[] = Array.isArray(data[bucket.key])
            ? data[bucket.key]
            : []
          return (
            <details
              key={bucket.key}
              className="border border-rule bg-paper-2 px-4 py-3 group open:bg-paper-2"
            >
              <summary className="flex cursor-pointer items-baseline gap-3 list-none">
                <span className="text-mute text-meta group-open:rotate-90 transition-transform inline-block w-3">
                  ›
                </span>
                <span className="font-display text-h4 text-ink">
                  {bucket.label}
                </span>
                <span className="text-meta text-mute">{bucket.desc}</span>
                <Badge variant="outline" className="ml-auto">
                  {items.length}
                </Badge>
              </summary>
              <div className="mt-3 pl-6 space-y-2">
                {items.length === 0 ? (
                  <p className="text-sm text-mute italic">
                    本项目在此桶无条目(可能是材料里没提,也可能 LLM 漏读 —
                    回上一步用「修订」反馈)
                  </p>
                ) : (
                  items.map((item, idx) => (
                    <div
                      key={idx}
                      className="border-l-2 border-rule pl-3 py-1"
                    >
                      <p className="text-sm text-ink leading-relaxed">
                        {item.content}
                      </p>
                      {(item.source_doc || item.section) && (
                        <p className="mt-1 text-meta text-mute">
                          {[item.source_doc, item.section]
                            .filter(Boolean)
                            .join(' · ')}
                        </p>
                      )}
                      {item.tags.length > 1 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {item.tags
                            .filter((t) => t !== bucket.key)
                            .map((t) => (
                              <Badge
                                key={t}
                                variant="outline"
                                className="text-[10px]"
                              >
                                也属:{t}
                              </Badge>
                            ))}
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </details>
          )
        })}
      </CardContent>
    </Card>
  )
}
