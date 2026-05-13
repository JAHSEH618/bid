import type { ReactNode } from 'react'
import { createBrowserRouter, Navigate } from 'react-router-dom'
import App from './App'
import { RequireAuth } from './components/RequireAuth'
import { AppShell } from './components/AppShell'
import { LoginPage } from './pages/LoginPage'
import { ChangePasswordPage } from './pages/ChangePasswordPage'
import { ProjectListPage } from './pages/ProjectListPage'
import { NewProjectPage } from './pages/NewProjectPage'
import { DocumentUploadPage } from './pages/DocumentUploadPage'
import { OutlineConfirmPage } from './pages/OutlineConfirmPage'
import { ChapterReviewPage } from './pages/ChapterReviewPage'
import { ProposalPage } from './pages/ProposalPage'
import { SettingsPage } from './pages/SettingsPage'
import { AdminPage } from './pages/AdminPage'
import { DesignTokensPage } from './pages/dev/DesignTokensPage'
import { MaterialUnderstandingPage } from './pages/MaterialUnderstandingPage'

// IMPLEMENTATION_SPEC §16.1 + REQUIREMENTS P0~P8。
// /change-password 用 allowMustChange 让 must_change_password=true 的用户能访问。
// AppShell 提供顶部导航 + DashScopeBanner;login / change-password 不挂壳。
// TODO(M4-后续): 把 Authed 拆到独立文件以满足 react-refresh / only-export-components,
// 当前 router.tsx 同时导出 router(常量)和 Authed(组件),HMR 边界不纯净。
// eslint-disable-next-line react-refresh/only-export-components
function Authed({
  children,
  shell = true,
}: {
  children: ReactNode
  shell?: boolean
}) {
  if (!shell) return <RequireAuth>{children}</RequireAuth>
  return (
    <RequireAuth>
      <AppShell>{children}</AppShell>
    </RequireAuth>
  )
}

export const router = createBrowserRouter([
  {
    element: <App />,
    children: [
      { path: '/login', element: <LoginPage /> },
      // PR-UI-1 验收用 — 不接入鉴权/AppShell,仅供 design tokens 预览。
      { path: '/dev/tokens', element: <DesignTokensPage /> },
      {
        path: '/change-password',
        element: (
          <RequireAuth allowMustChange>
            <ChangePasswordPage />
          </RequireAuth>
        ),
      },
      {
        path: '/',
        element: (
          <Authed>
            <ProjectListPage />
          </Authed>
        ),
      },
      {
        path: '/projects/new',
        element: (
          <Authed>
            <NewProjectPage />
          </Authed>
        ),
      },
      {
        path: '/projects/:id/upload',
        element: (
          <Authed>
            <DocumentUploadPage />
          </Authed>
        ),
      },
      {
        // PR-M8-1:材料理解评审页(extract_documents 后、generate_outline 前)
        path: '/projects/:id/understanding',
        element: (
          <Authed>
            <MaterialUnderstandingPage />
          </Authed>
        ),
      },
      {
        path: '/projects/:id/outline',
        element: (
          <Authed>
            <OutlineConfirmPage />
          </Authed>
        ),
      },
      {
        path: '/projects/:id/review',
        element: (
          <Authed>
            <ChapterReviewPage />
          </Authed>
        ),
      },
      {
        path: '/projects/:id/proposal',
        element: (
          <Authed>
            <ProposalPage />
          </Authed>
        ),
      },
      {
        path: '/settings',
        element: (
          <Authed>
            <SettingsPage />
          </Authed>
        ),
      },
      {
        path: '/admin',
        element: (
          <RequireAuth requireAdmin>
            <AppShell>
              <AdminPage />
            </AppShell>
          </RequireAuth>
        ),
      },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
])
