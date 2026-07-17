// Finyl-DCP API client — thin fetch wrapper with JWT + tenant-context headers.
// VITE_API_URL is empty in production (same-origin behind nginx) and
// http://localhost:8000 in local dev / docker-compose.
const BASE = import.meta.env.VITE_API_URL || "";

export function getToken() { return localStorage.getItem("finyl_token"); }
export function setToken(t) { t ? localStorage.setItem("finyl_token", t) : localStorage.removeItem("finyl_token"); }
export function getTenantOverride() { return localStorage.getItem("finyl_tenant"); }
export function setTenantOverride(id) { id ? localStorage.setItem("finyl_tenant", id) : localStorage.removeItem("finyl_tenant"); }

export class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; this.detail = detail; }
}

export async function api(path, { method = "GET", body, raw } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const tenant = getTenantOverride();
  if (tenant) headers["X-Tenant-Id"] = tenant;
  if (body) headers["Content-Type"] = "application/json";

  const res = await fetch(`${BASE}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401) { setToken(null); window.location.href = "/login"; throw new ApiError(401, "Session expired"); }
  if (raw) return res;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.detail || `Request failed (${res.status})`);
  return data;
}

export async function download(path, filename) {
  const res = await api(path, { raw: true });
  if (!res.ok) throw new ApiError(res.status, "Download failed");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

export const fmtKES = (n) =>
  "KES " + Number(n || 0).toLocaleString("en-KE", { maximumFractionDigits: 0 });
export const fmtDate = (d) => (d ? new Date(d).toLocaleDateString("en-KE", { year: "numeric", month: "short", day: "numeric" }) : "—");
