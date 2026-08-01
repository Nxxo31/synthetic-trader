const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || API_URL.replace(/^http/, 'ws');

export const getApiUrl = (): string => API_URL;

export async function fetchAPI<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`);
  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function postAPI<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function connectWebSocket(
  onMessage: (data: any) => void,
  onStatus: (status: 'connecting' | 'connected' | 'disconnected') => void,
): WebSocket | null {
  onStatus('connecting');
  try {
    const ws = new WebSocket(`${WS_URL}/ws/live-data`);

    ws.onopen = () => onStatus('connected');
    ws.onclose = () => {
      onStatus('disconnected');
      // Auto-reconnect after 3s
      setTimeout(() => connectWebSocket(onMessage, onStatus), 3000);
    };
    ws.onerror = () => onStatus('disconnected');
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        onMessage(msg);
      } catch {
        // ignore malformed messages
      }
    };

    return ws;
  } catch {
    onStatus('disconnected');
    return null;
  }
}
