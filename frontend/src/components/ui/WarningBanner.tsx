interface WarningBannerProps {
  message: string
}

export default function WarningBanner({ message }: WarningBannerProps) {
  return (
    <div role="status" className="rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
      {message}
    </div>
  )
}
