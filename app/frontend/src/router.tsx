import { createBrowserRouter, Navigate } from 'react-router-dom'
import App from './App'
import { PlaceholderPage } from './pages/PlaceholderPage'

// 路由骨架(IMPLEMENTATION_SPEC §16.1)。
// RequireAuth / 真实页面在 #28 / #25 / #24 / #27 接入,这里先用 PlaceholderPage 占位避免 RouterProvider 报错。
export const router = createBrowserRouter([
  {
    element: <App />,
    children: [
      { path: '/login', element: <PlaceholderPage title="登录" /> },
      {
        path: '/change-password',
        element: <PlaceholderPage title="修改密码" />,
      },
      { path: '/', element: <PlaceholderPage title="项目列表" /> },
      { path: '/projects/new', element: <PlaceholderPage title="新建项目" /> },
      {
        path: '/projects/:id/upload',
        element: <PlaceholderPage title="文档上传" />,
      },
      {
        path: '/projects/:id/outline',
        element: <PlaceholderPage title="大纲确认" />,
      },
      {
        path: '/projects/:id/review',
        element: <PlaceholderPage title="章节审核" />,
      },
      {
        path: '/projects/:id/proposal',
        element: <PlaceholderPage title="完整方案" />,
      },
      { path: '/settings', element: <PlaceholderPage title="设置" /> },
      { path: '/admin', element: <PlaceholderPage title="管理后台" /> },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
])
