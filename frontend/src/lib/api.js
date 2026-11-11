// Finyl-DCP API client — thin fetch wrapper with JWT + tenant-context headers.
// VITE_API_URL is empty in production (same-origin behind nginx) and
// http://localhost:8000 in local dev / docker-compose.
const BASE = import.meta.env.VITE_API_URL || "";

export function getToken() { return localStorage.getItem("finyl_token"); }
export function setToken(t) { t ? localStorage.setItem("finyl_token", t) : localStorage.removeItem("finyl_token"); }
export function getTenantOverride() { return localStorage.getItem("finyl_tenant"); }
export function setTenantOverride(id) { id ? localStorage.setItem("finyl_tenant", id) : localStorage.removeItem("finyl_tenant"); }

// WP7 (review 7.7): the security checks a DCP can switch on need two things the
// browser never sent. A stable id for this browser (device registration) and,
// when the DCP limits where staff may sign in from, the location.
export function getDeviceId() {
  try {
    let id = localStorage.getItem("finyl_device_id");
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`).replace(/-/g, "");
      localStorage.setItem("finyl_device_id", id);
    }
    return id;
  } catch { return ""; }
}
export function deviceCode() {
  const id = getDeviceId().toUpperCase().replace(/[^A-Z0-9]/g, "");
  return id ? `${id.slice(0, 4)}-${id.slice(4, 8)}` : "";
}
// { lat, lng, acc, at } once the person allowed location access. Kept for the
// browser tab (sessionStorage) so a page reload still sends it (WP8). WP11: `at`
// is when the position was taken; the server refuses one that is too old, so
// once a DCP asks for the location it is kept up to date while the tab is open.
let geo = (() => { try { return JSON.parse(sessionStorage.getItem("finyl_geo") || "null"); } catch { return null; } })();
export function setGeo(g) {
  geo = g;
  try { if (g) sessionStorage.setItem("finyl_geo", JSON.stringify(g)); else sessionStorage.removeItem("finyl_geo"); } catch { /* ignore */ }
}
// WP8: when the server ends a session it says why; the sign-in page shows it.
// WP11: several requests can be refused at once; only the first one sends the
// person to the sign-in page, so its reason is the one shown (a later "Not
// authenticated" used to replace it and reload the page a second time).
let ending = false;
async function sessionEnded(res) {
  let reason = "";
  try { const d = await res.clone().json(); if (typeof d.detail === "string") reason = d.detail; } catch { /* ignore */ }
  if (!ending) {
    ending = true;
    try { if (reason && reason !== "Token expired") sessionStorage.setItem("finyl_logout_reason", reason); } catch { /* ignore */ }
    setToken(null); window.location.href = "/login";
  }
  throw new ApiError(401, reason || "Session expired");
}
// The reason is removed a moment later rather than at once: the sign-in page can
// appear twice in a row (the app's own redirect, then the full page load), and
// the second one must still show it (WP11).
export function takeLogoutReason() {
  try {
    const r = sessionStorage.getItem("finyl_logout_reason") || "";
    if (r) setTimeout(() => { try { sessionStorage.removeItem("finyl_logout_reason"); } catch { /* ignore */ } }, 3000);
    return r;
  } catch { return ""; }
}
const fromPosition = (p) => ({ lat: p.coords.latitude, lng: p.coords.longitude,
  acc: p.coords.accuracy, at: Date.now() });
// Ask the browser for the location (only when the DCP requires it).
export function requestLocation() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) { reject(new Error("This browser cannot share its location.")); return; }
    navigator.geolocation.getCurrentPosition(
      (p) => { setGeo(fromPosition(p)); watchLocation(); resolve(geo); },
      () => reject(new Error("Location access was not allowed. Allow it in the browser and try again.")),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
  });
}
// WP11: keep the location fresh for the rest of the tab's life once the DCP has
// asked for it (the browser reports each move; nothing is sent until the next
// request to Finyl).
let watching = false;
function watchLocation() {
  if (watching || !navigator.geolocation) return;
  watching = true;
  try { sessionStorage.setItem("finyl_geo_watch", "1"); } catch { /* ignore */ }
  navigator.geolocation.watchPosition((p) => setGeo(fromPosition(p)), () => {},
    { enableHighAccuracy: true, maximumAge: 60000 });
}
try { if (sessionStorage.getItem("finyl_geo_watch")) watchLocation(); } catch { /* ignore */ }
function securityHeaders(headers) {
  const dev = getDeviceId();
  if (dev) headers["X-Device-Fingerprint"] = dev;
  if (geo) {
    headers["X-Geo-Lat"] = String(geo.lat); headers["X-Geo-Lng"] = String(geo.lng);
    if (geo.acc != null) headers["X-Geo-Acc"] = String(Math.round(geo.acc));
    if (geo.at) headers["X-Geo-Age"] = String(Math.max(0, Math.round((Date.now() - geo.at) / 1000)));
  }
}
// WP11: the server answers 428 {code: "location_required"} when the DCP needs a
// (fresh) location. Ask the browser once and send the request again.
const needsLocation = (status, data) => status === 428 && data?.detail?.code === "location_required";
async function withLocation(data) {
  try { await requestLocation(); }
  catch (e) { throw new ApiError(428, `${data.detail.message} (${e.message})`); }
}

export class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; this.detail = detail; }
}

// Auth endpoints authenticate the user; a 401/423 from them is a *login failure*
// (wrong password, locked account), NOT an expired session. We must surface the
// backend's real error message on these instead of clearing the token and
// redirecting with a misleading "Session expired". The session-expiry redirect
// stays in force for every other (post-login, authenticated) request.
const AUTH_ENDPOINTS = ["/api/v1/auth/login", "/api/v1/auth/login/otp", "/api/v1/auth/signup", "/api/v1/auth/change-password"];
function isAuthEndpoint(path) {
  return AUTH_ENDPOINTS.some((p) => path === p || path.startsWith(`${p}?`));
}

export async function api(path, { method = "GET", body, raw, retried } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const tenant = getTenantOverride();
  if (tenant) headers["X-Tenant-Id"] = tenant;
  if (body) headers["Content-Type"] = "application/json";
  securityHeaders(headers);

  const res = await fetch(`${BASE}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401 && !isAuthEndpoint(path)) return sessionEnded(res);
  if (res.status === 428 && !retried && !isAuthEndpoint(path)) {
    const d = await res.clone().json().catch(() => ({}));
    if (needsLocation(428, d)) { await withLocation(d); return api(path, { method, body, raw, retried: true }); }
  }
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
export async function upload(path, formData, retried = false) {
  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const tenant = getTenantOverride();
  if (tenant) headers["X-Tenant-Id"] = tenant;
  securityHeaders(headers);

  const res = await fetch(`${BASE}${path}`, { method: "POST", headers, body: formData });
  if (res.status === 401 && !isAuthEndpoint(path)) return sessionEnded(res);
  const data = await res.json().catch(() => ({}));
  if (needsLocation(res.status, data) && !retried) { await withLocation(data); return upload(path, formData, true); }
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

// Consent OTP — the officer requests a code (delivered to the client's phone),
// then verifies the code the client reads back. Both are officer-authenticated.
export const requestConsentOtp = (body) => api("/api/v1/clients/request-consent-otp", { method: "POST", body });
export const verifyConsentOtp = (body) => api("/api/v1/clients/verify-consent-otp", { method: "POST", body });

export const fmtKES = (n) =>
  "KES " + Number(n || 0).toLocaleString("en-KE", { maximumFractionDigits: 0 });
// WP10: the API sends every time with its zone ("...Z"). A time without a zone
// (older stored values, e.g. inside audit details) is UTC, so read it as UTC
// rather than as the browser's local time (which put it 3 hours off in Kenya).
const NAIVE_TIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;
export const toDate = (v) => (typeof v === "string" && NAIVE_TIME.test(v) ? new Date(v.replace(" ", "T") + "Z") : new Date(v));
// Today's date in the viewer's own time zone, as YYYY-MM-DD.
export const todayLocal = () => {
  const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 10);
};
export const fmtDate = (d) => (d ? toDate(d).toLocaleDateString("en-KE", { year: "numeric", month: "short", day: "numeric" }) : "—");
