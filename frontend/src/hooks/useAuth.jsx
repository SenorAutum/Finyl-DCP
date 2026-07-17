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
    const { access_token } = await api("/api/v1/auth/login", { method: "POST", body: { email, password } });
    setToken(access_token);
    setTenantOverride(null);
    await refresh();
  };

  const logout = () => { setToken(null); setTenantOverride(null); setUser(null); };

  const switchTenant = async (tenantId) => { setTenantOverride(tenantId); await refresh(); };

  // Role-based sidebar visibility (feature flags handled separately via user.modules)
  const roleAllows = (moduleKey) => {
    if (!user) return false;
    const role = user.role;
    if (role === "super_admin" || role === "tenant_admin") return true;
    if (role === "loan_officer") return ["lending", "crm", "dashboard", "payments", "impact"].includes(moduleKey);
    if (role === "call_agent") return ["call_center", "complaints"].includes(moduleKey);
    return false;
  };

  const canAccess = (moduleKey) => !!user?.modules?.[moduleKey] && roleAllows(moduleKey);

  return (
    <AuthCtx.Provider value={{ user, loading, login, logout, switchTenant, canAccess }}>
      {children}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => useContext(AuthCtx);
