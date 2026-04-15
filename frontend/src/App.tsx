import { useEffect, useState } from 'react'
import { checkHealth } from './api/client'

type HealthStatus = {
  status: string
  db: string
  pgvector: string
}

function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    checkHealth()
      .then(res => setHealth(res.data as HealthStatus))
      .catch(() => setError('Backend unreachable'))
  }, [])

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="text-center space-y-4">
        <h1 className="text-4xl font-bold text-gray-900">CareerPilot AI</h1>
        <p className="text-gray-500">AI-powered resume and job match platform</p>
        <div className="mt-8 p-4 bg-white rounded-lg shadow text-sm text-left space-y-1">
          {error ? (
            <p className="text-red-500">{error}</p>
          ) : health ? (
            <>
              <p>Status: <span className="font-mono text-green-600">{health.status}</span></p>
              <p>Database: <span className="font-mono text-green-600">{health.db}</span></p>
              <p>pgvector: <span className="font-mono text-green-600">{health.pgvector}</span></p>
            </>
          ) : (
            <p className="text-gray-400">Connecting to backend...</p>
          )}
        </div>
      </div>
    </div>
  )
}

export default App
