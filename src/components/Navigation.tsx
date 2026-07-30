import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Sun, Moon, LogIn, LogOut } from 'lucide-react';
import React from 'react';
import { useAuth } from '../context/AuthContext';

const ROUTES = [
  { path: '/', flag: 'upload' },
  { path: '/player', flag: 'studio' },
  { path: '/library', flag: 'library' },
] as const;

// N8 Terminal-command nav — routes read as CLI flags on a single prompt line,
// with a blinking caret at the end. See design.md § Nav.
export function Navigation({ theme, setTheme }: { theme: string, setTheme: React.Dispatch<React.SetStateAction<string>> }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { username, logout } = useAuth();
  const toggleTheme = () => setTheme(prev => (prev === 'dark' ? 'light' : 'dark'));

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  return (
    <header
      className="border-b-2 px-4 md:px-8 py-4 sticky top-0 z-50"
      style={{ backgroundColor: 'var(--bg)', borderBottomColor: 'var(--border-muted)' }}
    >
      <pre className="m-0 font-mono flex flex-wrap items-baseline gap-x-3 gap-y-1.5 text-sm md:text-base whitespace-pre-wrap">
        <span aria-hidden="true" style={{ color: 'var(--accent)' }}>{'>'}</span>

        <Link
          to="/"
          className="glitch font-bold tracking-widest uppercase"
          data-text="imagesound"
          style={{ color: 'var(--text-heading)' }}
        >
          imagesound
        </Link>

        {ROUTES.map(r => {
          const active = location.pathname === r.path;
          return (
            <Link
              key={r.path}
              to={r.path}
              style={{
                color: active ? 'var(--accent)' : 'var(--text-muted)',
                textDecoration: active ? 'underline' : 'none',
                textUnderlineOffset: '3px',
                transition: 'color var(--dur-micro) var(--ease-out)',
              }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.color = 'var(--accent)'; }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.color = 'var(--text-muted)'; }}
            >
              --{r.flag}
            </Link>
          );
        })}

        <button
          onClick={toggleTheme}
          className="inline-flex items-center gap-1 bg-transparent border-0 p-0 font-mono cursor-pointer"
          style={{ color: 'var(--text-muted)', transition: 'color var(--dur-micro) var(--ease-out)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
          title="Toggle theme"
        >
          --theme:{theme}
          {theme === 'light' ? <Sun size={14} /> : <Moon size={14} />}
        </button>

        {username ? (
          <button
            onClick={handleLogout}
            className="inline-flex items-center gap-1 bg-transparent border-0 p-0 font-mono cursor-pointer"
            style={{ color: 'var(--text-muted)', transition: 'color var(--dur-micro) var(--ease-out)' }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
            title="Log out"
          >
            --user:{username}
            <LogOut size={14} />
          </button>
        ) : (
          <Link
            to="/login"
            className="inline-flex items-center gap-1"
            style={{ color: 'var(--accent-secondary)', textDecoration: 'none' }}
            onMouseEnter={e => { e.currentTarget.style.textDecoration = 'underline'; }}
            onMouseLeave={e => { e.currentTarget.style.textDecoration = 'none'; }}
            title="Log in"
          >
            --login
            <LogIn size={14} />
          </Link>
        )}

        <span className="nav-term__caret" aria-hidden="true" style={{ color: 'var(--accent)' }}>▮</span>
      </pre>
    </header>
  );
}
