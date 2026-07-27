import { Link } from 'react-router-dom'

import { useAuth } from '../hooks/useAuth'

export default function DashboardPage() {
  const { user, logout } = useAuth()

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow">
        <div className="max-w-5xl mx-auto px-4 py-4 flex items-center justify-between">
          <h1 className="text-xl font-bold text-gray-900">CareerPilot AI</h1>
          <div className="flex items-center gap-4">
            <span className="text-sm text-gray-600">{user?.email}</span>
            <button
              onClick={() => void logout()}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100"
            >
              Log out
            </button>
          </div>
        </div>
      </header>
      <main className="max-w-5xl mx-auto px-4 py-12">
        <div className="bg-white rounded-lg shadow p-8 text-center space-y-2">
          <h2 className="text-lg font-semibold text-gray-900">
            Welcome{user?.full_name ? `, ${user.full_name}` : ''}!
          </h2>
          <p className="text-gray-500">
            Manage your resume and jobs below. Matching and AI features arrive in
            upcoming phases.
          </p>
          <div className="flex justify-center gap-3">
            <Link
              to="/resume"
              className="inline-block rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
            >
              Manage resume
            </Link>
            <Link
              to="/jobs"
              className="inline-block rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
            >
              Manage jobs
            </Link>
          </div>
        </div>
      </main>
    </div>
  )
}
