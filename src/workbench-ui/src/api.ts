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

export function eventSource(path: string): EventSource {
  // Browsers don't allow custom headers on EventSource. Embed identity in the
  // query string and have the backend honour it as an alternate auth signal,
  // OR (as we do now in the demo namespace) rely on cookie/session. For
  // deployment this should be replaced by a proper SSO bearer token in a
  // cookie. The notification stream here is a demo helper.
  const url = new URL(path, window.location.origin)
  url.searchParams.set('_uid', userStore.current.user_id)
  url.searchParams.set('_role', userStore.current.role)
  return new EventSource(url.toString())
}
