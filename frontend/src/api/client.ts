import { getApiKey } from '../auth/apiKeyStore';

let unauthorizedHandler: (() => void) | null = null;
let forbiddenHandler: ((message: string) => void) | null = null;

/** Registered by AuthGate on mount; called whenever an apiFetch response comes back 401
 *  (this browser's key was revoked/deleted elsewhere), so the gate can clear the stored
 *  key and fall back to the manual-entry form without every call site duplicating the check. */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

/** Registered by AuthGate on mount; called whenever an apiFetch response comes back 403
 *  (valid key, missing scope), so the gate can show a consistent user-facing message. */
export function setForbiddenHandler(fn: ((message: string) => void) | null): void {
  forbiddenHandler = fn;
}

export async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const key = getApiKey();
  const headers = new Headers(init?.headers);
  if (key) headers.set('X-Api-Key', key);
  const resp = await fetch(url, { ...init, headers });
  if (resp.status === 401) unauthorizedHandler?.();
  if (resp.status === 403) {
    const cloned = resp.clone();
    try {
      const data = await cloned.json();
      const message = typeof data?.detail === 'string' ? data.detail : "This API key doesn't have permission to do that.";
      forbiddenHandler?.(message);
    } catch {
      forbiddenHandler?.("This API key doesn't have permission to do that.");
    }
  }
  return resp;
}

export function withKeyParam(url: string): string {
  const key = getApiKey();
  if (!key) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}key=${encodeURIComponent(key)}`;
}

/** Opens an authenticated /ws connection (key carried as ?key=, the one endpoint that can't take
 *  a header). Shared by every hook that opens its own /ws socket (queue/orders/fleet). */
export function openAuthedWebSocket(): WebSocket {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const key = getApiKey();
  return new WebSocket(`${proto}//${window.location.host}/ws?key=${encodeURIComponent(key ?? '')}`);
}

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 30_000;

/** A live-data /ws connection that survives the server going away (restart, deploy, network blip).
 *  Messages go to `onMessage`; when the socket drops it reconnects with 1 s, 2 s, 4 s ... (max 30 s)
 *  back-off, and once a retried connection is open calls `onReconnect` so the caller can refetch whatever it
 *  missed. Returns a function that closes the socket for good. */
export function openLiveSocket(onMessage: (e: MessageEvent) => void, onReconnect?: () => void): () => void {
  let ws: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let closedByCaller = false;
  let attempts = 0;
  let delay = RECONNECT_MIN_MS;

  function connect() {
    const socket = openAuthedWebSocket();
    const isRetry = attempts++ > 0;   // anything after the first attempt may have missed events, even if the first never opened
    ws = socket;
    socket.onmessage = onMessage;
    socket.onopen = () => {
      delay = RECONNECT_MIN_MS;
      if (isRetry) onReconnect?.();
    };
    socket.onclose = () => {
      if (closedByCaller) return;
      timer = setTimeout(connect, delay);
      delay = Math.min(delay * 2, RECONNECT_MAX_MS);
    };
  }
  connect();

  return () => {
    closedByCaller = true;
    if (timer) clearTimeout(timer);
    ws?.close();
  };
}
