import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Sun, Moon, LogIn, LogOut } from 'lucide-react';
import React from 'react';
import { useAuth } from '../context/AuthContext';

const ROUTES = [
  { path: '/', flag: 'upload' },
  { path: '/player', flag: 'studio' },
  { path: '/library', flag: 'library' },
] as const;

// Pill-based nav — see docs/superpowers/specs/2026-08-04-y2k-aqua-redesign-design.md § Nav.
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
      className="px-4 md:px-8 py-3 sticky top-0 z-50"
      style={{ backgroundColor: 'var(--bg)', borderBottom: '1px solid var(--border)' }}
    >
      <nav className="m-0 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-sm md:text-base">
        <Link
          to="/"
          className="glitch font-bold tracking-widest uppercase mr-2"
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
              className="nav-term__btn px-3 py-1.5"
              style={{
                color: active ? 'var(--selected-text)' : 'var(--text-muted)',
                backgroundColor: active ? 'var(--accent)' : 'transparent',
              }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.color = 'var(--accent)'; }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.color = 'var(--text-muted)'; }}
            >
              {r.flag}
            </Link>
          );
        })}

        <button
          onClick={toggleTheme}
          className="nav-term__btn inline-flex items-center gap-1.5 bg-transparent border-0 px-3 py-1.5 cursor-pointer"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
          aria-label={`theme: ${theme}, toggle theme`}
          title="Toggle theme"
        >
          {theme === 'light' ? <Sun size={14} /> : <Moon size={14} />}
        </button>

        <span className="flex-grow" />

        {username ? (
          <button
            onClick={handleLogout}
            className="nav-term__btn inline-flex items-center gap-1.5 bg-transparent border-0 px-3 py-1.5 cursor-pointer"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
            aria-label={`user: ${username}, log out`}
            title="Log out"
          >
            {username}
            <LogOut size={14} />
          </button>
        ) : (
          <Link
            to="/login"
            className="nav-term__btn inline-flex items-center gap-1.5 px-3 py-1.5"
            style={{ color: 'var(--selected-text)', backgroundColor: 'var(--accent)', textDecoration: 'none' }}
            aria-label="log in"
            title="Log in"
          >
            Log in
            <LogIn size={14} />
          </Link>
        )}
      </nav>
    </header>
  );
}
