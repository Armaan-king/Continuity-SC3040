/** One API address for ordinary requests, downloads, and streamed runs. */
export const API_BASE = (import.meta.env.VITE_API_URL ?? '/api').replace(/\/$/, '')

export async function responseError(response: Response): Promise<string> {
  const body = await response.json().catch(() => null) as { detail?: unknown } | null
  if (typeof body?.detail === 'string') return body.detail
  if (Array.isArray(body?.detail)) {
    return body.detail.map((item: { msg?: string }) => item.msg ?? 'Invalid input').join('; ')
  }
  if (response.status === 401) return 'Please sign in again to continue.'
  if (response.status === 429) return 'The service is busy. Please try again shortly.'
  return `The server could not complete the request (${response.status}).`
}
