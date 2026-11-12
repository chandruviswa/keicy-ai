import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, session, SESSION_EXPIRED, type User } from "./api";

type AuthState = {
  user: User | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  can: (...roles: User["role"][]) => boolean;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Restore a saved session (if the token is still valid).
    if (!session.get()) {
      setReady(true);
      return;
    }
    api.me().then(setUser).catch(() => session.set(null)).finally(() => setReady(true));
  }, []);

  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener(SESSION_EXPIRED, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED, onExpired);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await api.login(email, password);
    session.set(res.access_token);
    setUser(res.user);
  }, []);

  const logout = useCallback(() => {
    session.set(null);
    setUser(null);
  }, []);

  const can = useCallback((...roles: User["role"][]) => !!user && (roles.includes(user.role) || user.role === "admin"), [user]);

  return <AuthContext.Provider value={{ user, ready, login, logout, can }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
