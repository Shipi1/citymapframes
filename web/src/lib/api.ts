// Typed wrappers around the FastAPI endpoints in scripts/server.py.
//
// URL layout
// ──────────
// Each call is fetch(`${API_BASE}${endpoint}`). The endpoint constants
// here intentionally DO NOT include `/api/` — that prefix lives in
// API_BASE so the same code works whether the app is mounted at:
//
//   dev:   /api/layers          (API_BASE='/api',     Vite dev-proxy)
//   prod:  /api/map/layers      (API_BASE='/api/map', host nginx rewrites)
//
// The default `/api` covers local development (Vite proxies `/api/*` to
// the uvicorn process — see vite.config.ts). The Docker build passes
// VITE_API_BASE=/api/map at build-time for production.

import type {
  Anchor,
  LayerRegistry,
  PlaceData,
  SharedDesign,
  ShareCreateResponse,
  ShareGetResponse,
  ShareListResponse,
} from './types';

const API_BASE = (import.meta.env.VITE_API_BASE ?? '/api') as string;

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
  return request<LayerRegistry>('/layers');
}

export function postPlace(query: string): Promise<Anchor> {
  return request<Anchor>('/place', {
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
  return request<PlaceData>('/data', {
    method: 'POST',
    body: JSON.stringify({ anchor, layers, radius_km: radiusKm, force }),
  });
}

export function createShare(
  design: SharedDesign,
  parent_id?: string,
): Promise<ShareCreateResponse> {
  return request<ShareCreateResponse>('/share', {
    method: 'POST',
    body: JSON.stringify({ design, parent_id }),
  });
}

export function getShare(id: string): Promise<ShareGetResponse> {
  return request<ShareGetResponse>(`/share/${encodeURIComponent(id)}`);
}

export function listShares(recent = 20, offset = 0): Promise<ShareListResponse> {
  return request<ShareListResponse>(
    `/share?recent=${recent}&offset=${offset}`,
  );
}

export { ApiError };
