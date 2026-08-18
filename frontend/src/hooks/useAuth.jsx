// Auth context: login state, user profile, tenant module flags, tenant switching.
import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api, getToken, setToken, setTenantOverride } from "../lib/api";

const AuthCtx = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!getToken()) { setUser(null); setLoading(false); return; }
    try { setUser(await api("/api/v1/auth/me")); }
    catch { setUser(null); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const login = async (email, password) => {
    const res = await api("/api/v1/auth/login", { method: "POST", body: { email, password } });
    setToken(res.access_token);
    setTenantOverride(null);
    await refresh();
    return res; // includes force_password_reset so the caller can route to /change-password
  };

  // AUTH-03/04: self-service password change. Returns a fresh token (old ones
  // are revoked server-side) and clears the forced-reset state.
  const changePassword = async (current_password, new_password) => {
    const res = await api("/api/v1/auth/change-password", {
      method: "POST", body: { current_password, new_password },
    });
    if (res.access_token) setToken(res.access_token);
    await refresh();
    return res;
  };

  const logout = async () => {
    try { await api("/api/v1/auth/logout", { method: "POST" }); } catch { /* best-effort revoke */ }
    setToken(null); setTenantOverride(null); setUser(null);
  };

  const switchTenant = async (tenantId) => { setTenantOverride(tenantId); await refresh(); };

  // Module -> permission keys. A role may use a module if it holds ANY of these.
  // This keeps the tenant feature-flag (user.modules) as the outer gate while the
  // permission model decides which roles see each module.
  const MODULE_PERMS = {
    dashboard: ["dashboard.company", "dashboard.region", "dashboard.branch", "dashboard.portfolio"],
    lending: ["clients.view_all", "clients.view_portfolio", "loans.view_all", "loans.view_portfolio"],
    payments: ["payments.upload", "disburse.execute", "reconcile.execute", "refund.execute"],
    impact: ["dashboard.company"],
  };

  // Role-based sidebar visibility (feature flags handled separately via user.modules).
  // Permission-driven for the core modules; legacy role fallback for engagement ones.
  const roleAllows = (moduleKey) => {
    if (!user) return false;
    const role = user.role;
    if (role === "super_admin" || role === "tenant_admin") return true;
    const perms = user.permissions || [];
    if (MODULE_PERMS[moduleKey]) return MODULE_PERMS[moduleKey].some((p) => perms.includes(p));
    // CRM & Field Sales pipeline is an operational, front-line feature. The
    // backend gates it on the tenant module flag only (require_module("crm")),
    // so every client-facing role — origination officers plus the managers /
    // HQ ops who oversee the pipeline — must be able to open it in the UI too.
    if (moduleKey === "crm") {
      return ["loan_officer", "relationship_officer", "branch_manager",
              "regional_manager", "hq_operations"].includes(role);
    }
    // Engagement modules stay role-scoped (legacy behaviour).
    if (role === "loan_officer") return ["lending", "crm", "dashboard", "payments", "impact"].includes(moduleKey);
    if (role === "call_agent") return ["call_center", "complaints"].includes(moduleKey);
    return false;
  };

  const canAccess = (moduleKey) => !!user?.modules?.[moduleKey] && roleAllows(moduleKey);

  // Permission-driven access — the primary RBAC gate. super_admin always passes.
  const can = useCallback((...keys) => {
    if (!user) return false;
    if (user.role === "super_admin") return true;
    const perms = user.permissions || [];
    if (perms.includes("*")) return true;
    // any-of semantics: pass if the user holds ANY of the requested keys
    return keys.some((k) => perms.includes(k));
  }, [user]);

  // all-of semantics helper
  const canAll = useCallback((...keys) => {
    if (!user) return false;
    if (user.role === "super_admin") return true;
    const perms = user.permissions || [];
    if (perms.includes("*")) return true;
    return keys.every((k) => perms.includes(k));
  }, [user]);

  const scope = user?.scope || null;

  return (
    <AuthCtx.Provider value={{ user, loading, login, logout, changePassword, switchTenant, canAccess, can, canAll, scope }}>
      {children}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => useContext(AuthCtx);

/** Conditionally render children when the user holds the given permission(s).
 *  <Can perm="users.manage">…</Can>  or  <Can any={["disburse.approve","refund.approve"]}>…</Can> */
export function Can({ perm, any, all, children, fallback = null }) {
  const { can, canAll } = useAuth();
  let ok = false;
  if (perm) ok = can(perm);
  else if (any) ok = can(...any);
  else if (all) ok = canAll(...all);
  return ok ? children : fallback;
}
