import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Lock, AlertCircle } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

/* Hallmark · component: login-form · genre: atmospheric · theme: design.md (custom-tuned)
 * scope: component-only pass (src/pages/Login.tsx) — tokens/markup only, per locked
 * design.md at project root; App-page family (Workbench discipline: small, functional
 * heading, no invented enrichment — see design.md § Per-page allowances).
 * states: default · hover · focus · active · disabled · loading · error · success
 */
export function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? '/';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="container mx-auto px-4 md:px-8 py-16 md:py-20 flex-grow flex items-start justify-center">
      <div className="w-full max-w-[28rem] flex flex-col gap-6">
        <div className="border-b-2 pb-4" style={{ borderBottomColor: 'var(--accent)' }}>
          <p
            className="font-mono text-xs uppercase tracking-widest opacity-70 mb-1"
            style={{ color: 'var(--accent-tertiary)' }}
          >
            // authentication required
          </p>
          <div className="flex items-center gap-3">
            <Lock className="w-7 h-7 shrink-0" strokeWidth={1} style={{ color: 'var(--accent)' }} />
            <h2
              className="text-2xl md:text-3xl font-display font-bold uppercase tracking-wide"
              style={{ color: 'var(--text-heading)' }}
            >
              Sys_Login
            </h2>
          </div>
        </div>

        <div
          data-collider
          className="border-4 p-8 md:p-10 flex flex-col gap-6"
          style={{ borderColor: 'var(--accent)', backgroundColor: 'transparent' }}
        >
          <form onSubmit={handleSubmit} className="flex flex-col gap-5" noValidate>
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="login-username"
                className="text-xs uppercase tracking-widest font-bold flex items-center gap-1.5"
                style={{ color: 'var(--accent)' }}
              >
                <span aria-hidden="true" style={{ opacity: 0.6 }}>&gt;</span> Username
              </label>
              <input
                id="login-username"
                type="text"
                className="brutal-input w-full font-mono text-sm"
                value={username}
                onChange={e => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
                disabled={submitting}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="login-password"
                className="text-xs uppercase tracking-widest font-bold flex items-center gap-1.5"
                style={{ color: 'var(--accent)' }}
              >
                <span aria-hidden="true" style={{ opacity: 0.6 }}>&gt;</span> Password
              </label>
              <input
                id="login-password"
                type="password"
                className="brutal-input w-full font-mono text-sm"
                value={password}
                onChange={e => setPassword(e.target.value)}
                autoComplete="current-password"
                disabled={submitting}
              />
            </div>

            {error && (
              <div
                role="alert"
                className="flex items-start gap-3 border-2 p-4"
                style={{
                  borderColor: 'var(--accent-secondary)',
                  color: 'var(--accent-secondary)',
                  backgroundColor: 'color-mix(in oklch, var(--accent-secondary) 8%, transparent)',
                }}
              >
                <AlertCircle size={20} className="shrink-0 mt-0.5" />
                <div>
                  <p className="font-bold uppercase tracking-widest text-sm">Access Denied</p>
                  <p className="text-xs mt-1 opacity-80">{error}</p>
                </div>
              </div>
            )}

            <button
              type="submit"
              disabled={submitting || !username || !password}
              className="brutal-btn flex items-center justify-center gap-2 disabled:opacity-30 mt-1"
            >
              {submitting ? 'LOGGING IN...' : 'LOGIN'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
