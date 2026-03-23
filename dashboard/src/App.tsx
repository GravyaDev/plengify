import { useState, useEffect } from 'react'
import { Routes, Route, Link, useLocation, Navigate } from 'react-router-dom'
import { LayoutDashboard, Globe, MessageCircle } from 'lucide-react'
import Dashboard from './pages/Dashboard'
import SitesPage from './pages/SitesPage'
import SiteDetailPage from './pages/SiteDetailPage'
import LoginPage from './pages/LoginPage'
import { cn } from './lib/utils'

const navItems = [
  { path: '/', label: 'Dashboard', icon: LayoutDashboard },
  { path: '/sites', label: 'Sites', icon: Globe },
]

function isAuthenticated() {
  return !!localStorage.getItem('pleng_auth')
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  if (!isAuthenticated()) return <Navigate to="/login" />
  return <>{children}</>
}

export default function App() {
  const location = useLocation()
  const [setup, setSetup] = useState<any>(null)

  useEffect(() => {
    fetch('/api/setup-status').then(r => r.json()).then(setSetup).catch(() => {})
  }, [])

  if (location.pathname === '/login') {
    return <LoginPage />
  }

  const botName = setup?.telegram_bot || ''

  return (
    <ProtectedRoute>
      <div className="flex h-screen">
        <aside className="w-52 bg-surface-800 border-r border-gray-700/50 flex flex-col">
          <div className="p-4 border-b border-gray-700/50">
            <h1 className="text-lg font-bold text-primary-400">Pleng</h1>
            <p className="text-xs text-gray-500">AI Platform Engineer</p>
          </div>
          <nav className="flex-1 p-2 space-y-1">
            {navItems.map(({ path, label, icon: Icon }) => (
              <Link key={path} to={path}
                className={cn(
                  'flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors',
                  location.pathname === path
                    ? 'bg-primary-600/20 text-primary-400'
                    : 'text-gray-400 hover:bg-gray-700/50 hover:text-gray-200'
                )}>
                <Icon size={18} />
                {label}
              </Link>
            ))}
          </nav>

          {/* Telegram bot link */}
          {botName && (
            <div className="p-3 border-t border-gray-700/50">
              <a href={`https://t.me/${botName}`} target="_blank" rel="noreferrer"
                className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-blue-400 hover:bg-blue-600/10 transition-colors">
                <MessageCircle size={16} />
                @{botName}
              </a>
            </div>
          )}

          <div className="p-3 border-t border-gray-700/50">
            <button
              onClick={() => { localStorage.removeItem('pleng_auth'); window.location.href = '/login' }}
              className="text-xs text-gray-500 hover:text-gray-300"
            >
              Logout
            </button>
          </div>
        </aside>

        <main className="flex-1 overflow-auto p-6">
          <Routes>
            <Route path="/" element={<Dashboard setup={setup} />} />
            <Route path="/sites" element={<SitesPage />} />
            <Route path="/sites/:id" element={<SiteDetailPage />} />
          </Routes>
        </main>
      </div>
    </ProtectedRoute>
  )
}
