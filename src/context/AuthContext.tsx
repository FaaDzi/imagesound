import { createContext, useContext, useState, useCallback, useEffect, useRef, ReactNode } from 'react';
import { getMe, loginRequest, logoutRequest } from '../api';

interface AuthContextValue {
  username: string | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [username, setUsername] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Bumped by every explicit login()/logout(). The background mount-check
  // below captures the value at the time it started; if a login/logout
  // happens before it resolves, its generation no longer matches and its
  // result is discarded instead of clobbering the newer, authoritative
  // state. Without this, a slow initial /auth/me (e.g. over a tunnel with
  // real network latency) can resolve AFTER a fast login and silently log
  // the user back out.
  const authGenRef = useRef(0);

  useEffect(() => {
    const myGen = authGenRef.current;
    getMe()
      .then(res => { if (authGenRef.current === myGen) setUsername(res.username); })
      .catch(() => { if (authGenRef.current === myGen) setUsername(null); })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (u: string, p: string) => {
    const res = await loginRequest(u, p);
    authGenRef.current += 1;
    setUsername(res.username);
  }, []);

  const logout = useCallback(async () => {
    await logoutRequest();
    authGenRef.current += 1;
    setUsername(null);
  }, []);

  return (
    <AuthContext.Provider value={{ username, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
