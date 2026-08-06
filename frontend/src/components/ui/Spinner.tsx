interface SpinnerProps {
  label?: string
}

/** 置中的載入指示：旋轉圓圈 + 可選文字。取代散落各頁的 "Loading…" 純文字。 */
export default function Spinner({ label = 'Loading…' }: SpinnerProps) {
  return (
    <div className="flex items-center justify-center gap-2 py-6 text-gray-400" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
      <span className="text-sm">{label}</span>
    </div>
  )
}
