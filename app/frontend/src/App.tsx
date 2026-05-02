import { Outlet } from 'react-router-dom'

// 应用外壳 - 各页面 RouterProvider 内 RequireAuth 之后渲染。
// 这里只做一个最薄的 Outlet 容器,后续 #28 任务接入 RequireAuth + DashScopeBanner。
export default function App() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <Outlet />
    </div>
  )
}
