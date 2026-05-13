import { Link } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardMeta,
  CardTitle,
} from '@/components/ui/card'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/hooks/useToast'

// v2 editorial design tokens 预览 — 仅 dev 路由，PR-UI-1 验收用。
// 不接入鉴权;不进入 AppShell,只展示 token 与 component variant。
export function DesignTokensPage() {
  return (
    <div className="min-h-screen bg-paper text-ink">
      <header className="border-b border-rule">
        <div className="mx-auto max-w-5xl px-gutter py-12">
          <p className="text-meta text-mute mb-3">
            Design system · Editorial · v2
          </p>
          <h1 className="text-hero">瑞典编辑风 / Design tokens</h1>
          <p className="mt-4 max-w-prose text-body text-mute">
            大留白 / 强层级 / 衬线大标题 + 无衬线正文 / 单色 + 1 个克制 accent /
            1px border 替代阴影。本页用于 PR-UI-1
            验收，可视化所有 token 与 component variant。
          </p>
          <div className="mt-6 flex items-center gap-4">
            <Link to="/" className="text-sm text-ink underline underline-offset-4">
              ← 返回首页
            </Link>
            <span className="text-meta text-mute">/dev/tokens</span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-gutter py-12 space-y-16">
        <ColorsSection />
        <TypographySection />
        <ButtonsSection />
        <FormSection />
        <CardsSection />
        <TabsSection />
        <DialogSection />
        <BadgesSection />
        <ToastSection />
      </main>
    </div>
  )
}

function Section({
  number,
  title,
  hint,
  children,
}: {
  number: string
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <section className="grid grid-cols-12 gap-gutter">
      <div className="col-span-12 md:col-span-3">
        <p className="text-meta text-mute">{number}</p>
        <h2 className="text-h2 mt-2">{title}</h2>
        {hint && <p className="mt-3 text-sm text-mute">{hint}</p>}
      </div>
      <div className="col-span-12 md:col-span-9 space-y-6">{children}</div>
    </section>
  )
}

function Swatch({
  name,
  value,
  className,
  textOn = 'text-ink',
}: {
  name: string
  value: string
  className: string
  textOn?: string
}) {
  return (
    <div className="border border-rule">
      <div className={`${className} h-20 border-b border-rule`} />
      <div className="px-3 py-2">
        <p className={`text-meta ${textOn}`}>{name}</p>
        <p className="font-mono text-xs text-mute mt-1">{value}</p>
      </div>
    </div>
  )
}

function ColorsSection() {
  return (
    <Section number="01" title="Color" hint="单色 + 1 个克制 rust accent。背景偏暖，文字接近纯黑但不刺眼。">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Swatch name="paper" value="#FAFAF7" className="bg-paper" />
        <Swatch name="paper-2" value="#F1EFE8" className="bg-paper-2" />
        <Swatch name="ink" value="#111111" className="bg-ink" />
        <Swatch name="mute" value="#6B6B66" className="bg-mute" />
        <Swatch name="accent (rust)" value="#B5471F" className="bg-accent" />
        <Swatch name="warn" value="#8B5A00" className="bg-warn" />
        <Swatch name="rule" value="ink @ 10%" className="bg-rule" />
        <Swatch name="destructive" value="hsl(0 70% 42%)" className="bg-destructive" />
      </div>
    </Section>
  )
}

function TypographySection() {
  return (
    <Section
      number="02"
      title="Typography"
      hint="衬线大标题 + 无衬线正文。Type scale 跨度大，体现层级。"
    >
      <div className="space-y-6">
        <div>
          <p className="text-meta text-mute mb-2">text-hero · 64 / 1.05</p>
          <p className="text-hero">投标技术方案生成器</p>
        </div>
        <div>
          <p className="text-meta text-mute mb-2">text-h1 · 40 / 1.1</p>
          <p className="text-h1">第一章 · 公司基本情况</p>
        </div>
        <div>
          <p className="text-meta text-mute mb-2">text-h2 · 28 / 1.2</p>
          <p className="text-h2">技术方案与实施计划</p>
        </div>
        <div>
          <p className="text-meta text-mute mb-2">text-h3 · 20 / 1.3</p>
          <p className="text-h3">2.1 项目背景</p>
        </div>
        <div>
          <p className="text-meta text-mute mb-2">text-body · 16 / 1.6 · max-w-prose</p>
          <p className="text-body max-w-prose">
            本项目针对招标文件中提出的核心功能需求，结合现有的技术积累与行业最佳实践，
            提出分阶段、可验证的实施方案。整体设计强调可维护性、可扩展性与安全合规，
            为后续运维与持续迭代奠定基础。
          </p>
        </div>
        <div>
          <p className="text-meta text-mute mb-2">text-meta · 13 / 1.4 · uppercase tracking</p>
          <p className="text-meta">Project · 2026 · IN PROGRESS</p>
        </div>
      </div>
    </Section>
  )
}

function ButtonsSection() {
  return (
    <Section number="03" title="Button" hint="default = ink 填充；secondary/outline = 1px ink border；ghost/link = underline 文本按钮；accent 保留给关键 CTA。">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button>默认按钮</Button>
          <Button variant="secondary">次级</Button>
          <Button variant="outline">Outline</Button>
          <Button variant="ghost">Ghost link</Button>
          <Button variant="link">Link</Button>
          <Button variant="subtle">Subtle</Button>
          <Button variant="accent">锁定目录</Button>
          <Button variant="destructive">删除</Button>
          <Button variant="success">通过</Button>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button size="xs">XS</Button>
          <Button size="sm">SM</Button>
          <Button>Default</Button>
          <Button size="lg">Large</Button>
          <Button disabled>Disabled</Button>
        </div>
      </div>
    </Section>
  )
}

function FormSection() {
  return (
    <Section number="04" title="Form controls" hint="无 border，仅底部 1px line；focus 时 line 加粗到 2px + 颜色变 accent。">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-prose">
        <div className="space-y-2">
          <Label htmlFor="demo-project">项目名称</Label>
          <Input id="demo-project" placeholder="例如:某地综合管理平台" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="demo-org">投标单位</Label>
          <Input id="demo-org" defaultValue="某技术服务有限公司" />
        </div>
        <div className="space-y-2 md:col-span-2">
          <Label htmlFor="demo-feedback">章节反馈</Label>
          <Textarea
            id="demo-feedback"
            placeholder="对当前章节有何调整建议?"
            rows={4}
          />
        </div>
      </div>
    </Section>
  )
}

function CardsSection() {
  return (
    <Section number="05" title="Card" hint="1px border 替代 shadow，paper-2 次级背景；header serif；meta uppercase tracking。">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-gutter">
        <Card>
          <CardHeader>
            <CardMeta>项目 #042 · 2026.05</CardMeta>
            <CardTitle>某市政管理平台投标</CardTitle>
            <CardDescription>
              基于 LangGraph 工作流的章节级生成与人工评审。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm">章节进度 12 / 24,等待评审 3 章。</p>
          </CardContent>
          <CardFooter>
            <Button variant="ghost" size="sm">查看详情</Button>
          </CardFooter>
        </Card>
        <Card className="bg-paper-2">
          <CardHeader>
            <CardMeta>Status · awaiting review</CardMeta>
            <CardTitle>第三章 实施计划</CardTitle>
            <CardDescription>LLM-2 已生成完毕,等待用户决策。</CardDescription>
          </CardHeader>
          <CardFooter>
            <Button variant="success" size="sm">通过</Button>
          </CardFooter>
        </Card>
      </div>
    </Section>
  )
}

function TabsSection() {
  return (
    <Section number="06" title="Tabs" hint="底部 1px line indicator，active 加粗到 3px;不用胶囊背景。">
      <Tabs defaultValue="overview" className="max-w-prose">
        <TabsList>
          <TabsTrigger value="overview">总览</TabsTrigger>
          <TabsTrigger value="outline">目录</TabsTrigger>
          <TabsTrigger value="chapters">章节</TabsTrigger>
          <TabsTrigger value="export">导出</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="text-sm text-mute">
          项目当前处于「等待目录确认」阶段。
        </TabsContent>
        <TabsContent value="outline">
          点击「目录」tab 查看 LLM-1 输出。
        </TabsContent>
        <TabsContent value="chapters">章节列表 placeholder。</TabsContent>
        <TabsContent value="export">导出 .docx placeholder。</TabsContent>
      </Tabs>
    </Section>
  )
}

function DialogSection() {
  return (
    <Section number="07" title="Dialog" hint="无圆角、无阴影;左侧 8px accent 色条作为视觉锚点。">
      <Dialog>
        <DialogTrigger asChild>
          <Button variant="secondary">打开 Dialog</Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除项目</DialogTitle>
            <DialogDescription>
              此操作不可撤销,所有相关章节、材料、导出文件都会被清理。
            </DialogDescription>
          </DialogHeader>
          <div className="text-sm text-mute">
            如确认,请在下方输入项目名以二次确认。
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="ghost">取消</Button>
            </DialogClose>
            <Button variant="destructive">确认删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Section>
  )
}

function BadgesSection() {
  return (
    <Section number="08" title="Badge" hint="极简标签 — 1px hairline 边框 + uppercase tracking。">
      <div className="flex flex-wrap items-center gap-2">
        <Badge>Default</Badge>
        <Badge variant="secondary">Secondary</Badge>
        <Badge variant="outline">Outline</Badge>
        <Badge variant="muted">Muted</Badge>
        <Badge variant="accent">Accent</Badge>
        <Badge variant="success">PASS</Badge>
        <Badge variant="warning">REVISE</Badge>
        <Badge variant="warn">占位符</Badge>
        <Badge variant="destructive">FAILED</Badge>
      </div>
    </Section>
  )
}

function ToastSection() {
  const { toast } = useToast()
  return (
    <Section number="09" title="Toast" hint="极简 — 纯文本 + 顶部 1px accent line。">
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="secondary"
          onClick={() =>
            toast({
              title: '默认提示',
              description: '这是一条标准 toast。',
            })
          }
        >
          Default toast
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() =>
            toast({
              variant: 'success',
              title: '章节已通过',
              description: '第三章 实施计划 已标记 pass,可以导出。',
            })
          }
        >
          Success
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() =>
            toast({
              variant: 'destructive',
              title: '导出失败',
              description: 'Mermaid 图渲染异常,请重试一次。',
            })
          }
        >
          Destructive
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() =>
            toast({
              variant: 'warning',
              title: '占位符未替换',
              description: '本章节含 3 处占位符,导出前请手动替换。',
            })
          }
        >
          Warning
        </Button>
      </div>
      <Separator className="my-4" />
      <p className="text-meta text-mute">点击按钮触发不同 variant 的 toast。</p>
    </Section>
  )
}
