import { parseSSE } from './sse'

/** Every api failure, normalised: the envelope the api sends, or what we
 *  can tell from the response when it isn't JSON (e.g. an nginx 502 page). */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId?: string
  readonly retryAfterS?: number

  constructor(status: number, code: string, message: string, requestId?: string, retryAfterS?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
    this.retryAfterS = retryAfterS
  }
}

export async function toApiError(res: Response): Promise<ApiError> {
  let code = `http_${res.status}`
  let message = res.statusText || 'request failed'
  let requestId = res.headers.get('x-request-id') ?? undefined
  try {
    const body = await res.json()
    code = body.error?.code ?? code
    message = body.error?.message ?? message
    requestId = body.error?.request_id ?? requestId
  } catch {
    // Not JSON: keep what the status line and headers say.
  }
  const retryAfter = Number(res.headers.get('retry-after'))
  return new ApiError(
    res.status,
    code,
    message,
    requestId,
    Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined,
  )
}

/** A runbook section an answer cited, as [R1] in its text. */
export interface Citation {
  ref: string
  runbookId: number
  title: string
  /** The heading path, "Disk full > Free space". */
  heading: string
}

export type ChatEvent =
  | {
      type: 'meta'
      requestId: string
      model: string
      /** null: not known in advance - the agent chooses what to read (CHAT_MODE=agent). */
      alertsInContext: number | null
      runbooksInContext: number
      /** "hybrid", "keyword_only" (the question could not be embedded), or null (runbooks off). */
      retrieval: string | null
    }
  | { type: 'token'; delta: string }
  /** A tool the agent called: what it read, as counts (CHAT_MODE=agent). */
  | { type: 'tool'; name: string; ok: boolean; summary: string }
  | {
      type: 'done'
      ttftMs: number | null
      durationMs: number | null
      completionTokens: number | null
      /** "length": the answer was cut off by the output limit. */
      finishReason: string | null
      citations: Citation[]
    }
  | { type: 'error'; code: string; message: string; requestId: string }

/**
 * POSTs a question and yields the answer as it streams. Errors before the
 * stream starts (rate limit, validation, database down) throw ApiError;
 * errors after it started arrive as an `error` event. Aborting the signal
 * stops the stream and closes the connection.
 */
export async function* streamChat(message: string, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ message }),
    signal,
  })
  if (!res.ok || !res.body) throw await toApiError(res)

  let finished = false
  for await (const { event, data } of parseSSE(res.body)) {
    const payload = JSON.parse(data)
    switch (event) {
      case 'meta':
        yield {
          type: 'meta',
          requestId: payload.request_id,
          model: payload.model,
          alertsInContext: payload.alerts_in_context,
          runbooksInContext: payload.runbooks_in_context ?? 0,
          retrieval: payload.retrieval ?? null,
        }
        break
      case 'token':
        yield { type: 'token', delta: payload.delta }
        break
      case 'tool':
        yield { type: 'tool', name: payload.name, ok: payload.ok, summary: payload.summary }
        break
      case 'done':
        finished = true
        yield {
          type: 'done',
          ttftMs: payload.ttft_ms,
          durationMs: payload.duration_ms,
          completionTokens: payload.usage?.completion_tokens ?? null,
          finishReason: payload.finish_reason ?? null,
          citations: (payload.citations ?? []).map(
            (c: { ref: string; runbook_id: number; title: string; heading: string }) => ({
              ref: c.ref,
              runbookId: c.runbook_id,
              title: c.title,
              heading: c.heading,
            }),
          ),
        }
        break
      case 'error':
        finished = true
        yield { type: 'error', code: payload.code, message: payload.message, requestId: payload.request_id }
        break
    }
  }
  // The connection closed without the api's closing event: a worker was
  // killed, a proxy timed out, or a deploy cut the stream. Without this the
  // UI would wait forever on a stream that is gone.
  if (!finished) {
    throw new ApiError(0, 'stream_incomplete', 'The answer was cut off before it finished.')
  }
}

export type Severity = 'info' | 'warning' | 'high' | 'critical'

export interface Alert {
  id: number
  team: string
  source: string
  severity: Severity
  message: string
  created_at: string
}

/** Newest first, from every team the user can see, or only `team`. */
export async function fetchAlerts(limit = 20, team?: string): Promise<Alert[]> {
  const params = new URLSearchParams({ limit: String(limit), ...(team ? { team } : {}) })
  const res = await fetch(`/api/alerts?${params}`)
  if (!res.ok) throw await toApiError(res)
  const page: { items: Alert[] } = await res.json()
  return page.items
}

export type NewAlert = Pick<Alert, 'team' | 'source' | 'severity' | 'message'>

export async function createAlert(alert: NewAlert): Promise<Alert> {
  // The browser adds an Origin header to this POST; the api refuses
  // state-changing requests without this site's (CSRF protection).
  const res = await fetch('/api/alerts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(alert),
  })
  if (!res.ok) throw await toApiError(res)
  return res.json()
}

// --- The assistant's off switch (ADR-0024) ------------------------------------

export interface AssistantStatus {
  enabled: boolean
  /** While it is off: why, as the org admin who switched it off wrote it. */
  reason: string | null
  changed_at: string
}

export async function fetchAssistant(): Promise<AssistantStatus> {
  const res = await fetch('/api/assistant')
  if (!res.ok) throw await toApiError(res)
  return res.json()
}

/** Org admins only; switching it off needs a reason, which everyone sees. */
export async function setAssistant(enabled: boolean, reason?: string): Promise<AssistantStatus> {
  const res = await fetch('/api/assistant', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled, reason: reason || null }),
  })
  if (!res.ok) throw await toApiError(res)
  return res.json()
}

// --- Who is signed in -------------------------------------------------------
// The session is an httpOnly cookie: this code never sees it, and never needs
// to. It only asks the api who the cookie belongs to.

export type Role = 'viewer' | 'responder' | 'admin'

const RANK: Record<Role, number> = { viewer: 1, responder: 2, admin: 3 }

/** Does `role` include everything `needed` may do? Roles are ranked. */
export const atLeast = (role: Role, needed: Role) => RANK[role] >= RANK[needed]

export interface Team {
  slug: string
  name: string
  role: Role
}

export interface Me {
  id: number
  email: string | null
  name: string | null
  org_admin: boolean
  /** Every team the user can see, with their role in it (an org admin: all). */
  teams: Team[]
}

/** The signed-in user, or null when nobody is. */
export async function fetchMe(): Promise<Me | null> {
  const res = await fetch('/api/me')
  if (res.status === 401) return null
  if (!res.ok) throw await toApiError(res)
  return res.json()
}

/** Signing in is a page navigation, not a fetch: the identity provider shows
 *  its own login page, then sends the browser back to `next`. */
export function signInUrl(next = window.location.pathname): string {
  return `/api/auth/login?next=${encodeURIComponent(next)}`
}

/** Ends the session here; resolves to the page that ends it at the identity
 *  provider too (or "/" when it has none), for the browser to visit. */
export async function signOut(): Promise<string> {
  const res = await fetch('/api/auth/logout', { method: 'POST' })
  if (!res.ok) throw await toApiError(res)
  const body: { logout_url: string } = await res.json()
  return body.logout_url
}
