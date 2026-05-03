import { userStore } from './userStore'

function authHeaders(): HeadersInit {
  return {
    'X-User-Id': userStore.current.user_id,
    'X-User-Role': userStore.current.role,
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: authHeaders() })
  if (!res.ok) {
    let body: any = ''
    try { body = await res.json() } catch (_) { body = await res.text() }
    throw new Error(`GET ${path} → ${res.status}: ${typeof body === 'string' ? body : (body.detail ?? JSON.stringify(body))}`)
  }
  return res.json() as Promise<T>
}

export async function apiPost<T>(path: string, payload: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    let body: any = ''
    try { body = await res.json() } catch (_) { body = await res.text() }
    throw new Error(`POST ${path} → ${res.status}: ${typeof body === 'string' ? body : (body.detail ?? JSON.stringify(body))}`)
  }
  return res.json() as Promise<T>
}

export async function apiBlob(path: string): Promise<Blob> {
  const res = await fetch(path, { headers: authHeaders() })
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`)
  return res.blob()
}

export async function eventSource(path: string): Promise<EventSource> {
  // Browsers cannot send custom headers on EventSource. We first POST to the
  // token endpoint (with normal auth headers) to get a short-lived opaque
  // token, then open the stream URL with only that token in the query string.
  const { token } = await apiPost<{ token: string }>('/api/_demo/notifications/token', {})
  const url = new URL(path, window.location.origin)
  url.searchParams.set('token', token)
  return new EventSource(url.toString())
}
