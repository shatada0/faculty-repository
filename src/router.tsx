import { createBrowserRouter } from 'react-router'
import { AdminLayout, NotFound, PublicLayout, RequireAuth, RouteError } from '@/components/Layout'
import Home from '@/routes/Home'
import Catalog from '@/routes/Catalog'
import WorkPage from '@/routes/WorkPage'
import People from '@/routes/People'
import Login from '@/routes/Login'

// Admin screens and the profile chart are code-split to keep the catalog's first paint small.
const page = (load: () => Promise<{ default: React.ComponentType }>) => async () => ({ Component: (await load()).default })

export const router = createBrowserRouter([
  {
    element: <PublicLayout />,
    errorElement: <RouteError />,
    children: [
      { path: '/', element: <Home /> },
      { path: '/works', element: <Catalog /> },
      { path: '/works/:id', element: <WorkPage /> },
      { path: '/people', element: <People /> },
      { path: '/people/:id', lazy: page(() => import('@/routes/Person')) },
      { path: '/login', element: <Login /> },
      { path: '/my', lazy: page(() => import('@/routes/MyWorks')) },
      { path: '*', element: <NotFound /> },
    ],
  },
  {
    path: '/admin',
    element: <RequireAuth><AdminLayout /></RequireAuth>,
    errorElement: <RouteError />,
    children: [
      { index: true, lazy: page(() => import('@/routes/admin/Dashboard')) },
      { path: 'imports', lazy: page(() => import('@/routes/admin/Imports')) },
      { path: 'imports/new', lazy: page(() => import('@/routes/admin/ImportNew')) },
      { path: 'imports/:id', lazy: page(() => import('@/routes/admin/ImportDetail')) },
      { path: 'review', lazy: page(() => import('@/routes/admin/Review')) },
      { path: 'works', lazy: page(() => import('@/routes/admin/Works')) },
      { path: 'works/new', lazy: page(() => import('@/routes/admin/WorkEditor')) },
      { path: 'works/:id', lazy: page(() => import('@/routes/admin/WorkEditor')) },
      { path: 'people', lazy: page(() => import('@/routes/admin/People')) },
      { path: 'people/:id', lazy: page(() => import('@/routes/admin/PersonAdmin')) },
      { path: '*', element: <NotFound /> },
    ],
  },
])
