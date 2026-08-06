import { useEffect, useState } from 'react'

interface ElapsedTimerProps {
  /** 補充說明，例如「Kit generation usually takes 30–90 s」。 */
  hint?: string
}

/**
 * 長任務進度指示（NFR-2）：不確定進度條 + 經過秒數。
 * 掛載即開始計時，任務結束由父層 unmount。
 */
export default function ElapsedTimer({ hint }: ElapsedTimerProps) {
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    const id = window.setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => window.clearInterval(id)
  }, [])

  return (
    <div className="space-y-1" role="status">
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-blue-100">
        <div className="h-full w-1/3 animate-pulse rounded-full bg-blue-500" />
      </div>
      <p className="text-xs text-gray-500">
        {seconds}s elapsed{hint ? ` — ${hint}` : ''}
      </p>
    </div>
  )
}
