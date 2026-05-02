// M4-1 占位页:#25 / #27 / #28 任务里替换为实际页面组件。
export function PlaceholderPage({ title }: { title: string }) {
  return (
    <div className="container py-12">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="mt-4 text-sm text-muted-foreground">
        此页面将在后续任务实现。
      </p>
    </div>
  )
}
