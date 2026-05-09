// Typed wrappers around the FastAPI endpoints in scripts/server.py.
// Calls go through Vite's dev proxy in development; in production the
// reverse proxy handles the same path prefix (see TODO.md).

import type {
  Anchor,
  LayerRegistry,
  PlaceData,
  SharedDesign,
  ShareCreateResponse,
  ShareGetResponse,
  ShareListResponse,
} from './types';

// API base path. Empty string means same-origin; in dev that's the
// Vite dev server, which proxies /api/* to the FastAPI process.
// In production we'll deploy at e.g. shipisnature.com/api/map/* and
// override this via a build-time env var.
const API_BASE = (import.meta.env.VITE_API_BASE ?? '') as string;

class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* body wasn't JSON — keep status text */
    }
    throw new ApiError(resp.status, detail);
  }
  return (await resp.json()) as T;
}

export function getLayers(): Promise<LayerRegistry> {
  return request<LayerRegistry>('/api/layers');
}

export function postPlace(query: string): Promise<Anchor> {
  return request<Anchor>('/api/place', {
    method: 'POST',
    body: JSON.stringify({ query }),
  });
}

export function postData(
  anchor: Anchor,
  layers?: string[],
  radiusKm = 30,
  force = false,
): Promise<PlaceData> {
  return request<PlaceData>('/api/data', {
    method: 'POST',
    body: JSON.stringify({ anchor, layers, radius_km: radiusKm, force }),
  });
}

export function createShare(
  design: SharedDesign,
  parent_id?: string,
): Promise<ShareCreateResponse> {
  return request<ShareCreateResponse>('/api/share', {
    method: 'POST',
    body: JSON.stringify({ design, parent_id }),
  });
}

export function getShare(id: string): Promise<ShareGetResponse> {
  return request<ShareGetResponse>(`/api/share/${encodeURIComponent(id)}`);
}

export function listShares(recent = 20, offset = 0): Promise<ShareListResponse> {
  return request<ShareListResponse>(
    `/api/share?recent=${recent}&offset=${offset}`,
  );
}

export { ApiError };
