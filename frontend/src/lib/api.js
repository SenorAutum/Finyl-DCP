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
  if (!res.ok) throw new ApiError(res.status, errorDetail(res.status, data.detail));
  return data;
}

// AUTH-03: the backend blocks a user whose password reset is forced with a
// 403 whose detail is {code: "password_reset_required"}. Route them to the
// change-password screen and surface a readable message everywhere else.
export function errorDetail(status, detail) {
  if (status === 403 && detail && typeof detail === "object" && detail.code === "password_reset_required") {
    if (!window.location.pathname.startsWith("/change-password")) {
      window.location.href = "/change-password";
    }
    return detail.message || "Password reset required";
  }
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") return detail.message || JSON.stringify(detail);
  return `Request failed (${status})`;
}

// Multipart upload — the browser must set its own multipart boundary, so we
// deliberately do NOT send a Content-Type header here.
export async function upload(path, formData) {
  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const tenant = getTenantOverride();
  if (tenant) headers["X-Tenant-Id"] = tenant;

  const res = await fetch(`${BASE}${path}`, { method: "POST", headers, body: formData });
  if (res.status === 401) { setToken(null); window.location.href = "/login"; throw new ApiError(401, "Session expired"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, errorDetail(res.status, data.detail));
  return data;
}

// Authenticated blob URL — used to preview saved document images inline.
export async function blobUrl(path) {
  const res = await api(path, { raw: true });
  if (!res.ok) throw new ApiError(res.status, "Could not load file");
  return URL.createObjectURL(await res.blob());
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
