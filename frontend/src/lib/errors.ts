import axios from 'axios'

/** FastAPI 422 validation error 的單筆形狀。 */
interface ValidationDetailItem {
  msg?: string
  loc?: (string | number)[]
}

interface ApiErrorBody {
  detail?: string | ValidationDetailItem[]
}

const STATUS_FALLBACKS: Record<number, string> = {
  401: 'Your session has expired. Please sign in again.',
  403: 'You do not have access to this resource.',
  404: 'Not found.',
  409: 'This conflicts with the current state. Please refresh and retry.',
  413: 'The file or text is too large.',
  415: 'Unsupported file type — only PDF and DOCX are accepted.',
  422: 'The input could not be processed. Please check it and retry.',
  429: 'Too many requests — please wait a moment and try again.',
  500: 'Server error — please try again later.',
  503: 'The service is temporarily unavailable. Please try again shortly.',
}

/**
 * 從任何錯誤（axios / 其他）萃取一句人類可讀的訊息。
 *
 * 順序：後端 `{detail: string}` → 422 `{detail: [{msg}]}` → 狀態碼 fallback
 * → 網路層（timeout / 斷線）→ 呼叫端 fallback。
 */
export function getErrorMessage(err: unknown, fallback = 'Something went wrong. Please try again.'): string {
  if (axios.isAxiosError(err)) {
    const response = err.response
    if (!response) {
      return err.code === 'ECONNABORTED'
        ? 'The request timed out — the server may be busy. Please try again.'
        : 'Network error — cannot reach the server. Check your connection.'
    }
    const body = response.data as ApiErrorBody | undefined
    if (typeof body?.detail === 'string' && body.detail.trim()) {
      return body.detail
    }
    if (Array.isArray(body?.detail)) {
      const msgs = body.detail
        .map((item) => item.msg)
        .filter((msg): msg is string => Boolean(msg))
      if (msgs.length > 0) return msgs.join('; ')
    }
    const byStatus = STATUS_FALLBACKS[response.status]
    if (byStatus) return byStatus
    if (response.status >= 500) return STATUS_FALLBACKS[500]
  }
  return fallback
}
