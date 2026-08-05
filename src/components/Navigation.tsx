import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Sun, Moon, LogIn, LogOut, Circle } from 'lucide-react';
import React from 'react';
import { useAuth } from '../context/AuthContext';

const ROUTES = [
  { path: '/', flag: 'upload' },
  { path: '/player', flag: 'studio' },
  { path: '/library', flag: 'library' },
] as const;

// N8 Terminal-command nav — routes read as CLI flags on a single prompt line,
// with a blinking caret at the end. See design.md § Nav.
export function Navigation({ theme, setTheme, physicsOn, setPhysicsOn }: {
  theme: string,
  setTheme: React.Dispatch<React.SetStateAction<string>>,
  physicsOn: boolean,
  setPhysicsOn: React.Dispatch<React.SetStateAction<boolean>>,
}) {
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
              className="nav-term__btn"
              style={{
                color: active ? 'var(--accent)' : 'var(--text-muted)',
                textDecoration: active ? 'underline' : 'none',
                textUnderlineOffset: '3px',
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
          className="nav-term__btn inline-flex items-center gap-1 bg-transparent border-0 p-0 font-mono cursor-pointer"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
          aria-label={`--theme:${theme}, toggle theme`}
          title="Toggle theme"
        >
          --theme:{theme}
          {theme === 'light' ? <Sun size={14} /> : <Moon size={14} />}
        </button>

        <button
          onClick={() => setPhysicsOn(prev => !prev)}
          className="nav-term__btn inline-flex items-center gap-1 bg-transparent border-0 p-0 font-mono cursor-pointer"
          style={{ color: physicsOn ? 'var(--accent)' : 'var(--text-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
          onMouseLeave={e => { if (!physicsOn) e.currentTarget.style.color = 'var(--text-muted)'; }}
          aria-label={`--physics:${physicsOn ? 'on' : 'off'}, toggle physics ball toy`}
          title="Toggle physics ball toy"
        >
          --physics:{physicsOn ? 'on' : 'off'}
          <Circle size={14} />
        </button>

        {username ? (
          <button
            onClick={handleLogout}
            className="nav-term__btn inline-flex items-center gap-1 bg-transparent border-0 p-0 font-mono cursor-pointer"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
            aria-label={`--user:${username}, log out`}
            title="Log out"
          >
            --user:{username}
            <LogOut size={14} />
          </button>
        ) : (
          <Link
            to="/login"
            className="nav-term__btn inline-flex items-center gap-1"
            style={{ color: 'var(--accent-secondary)', textDecoration: 'none' }}
            onMouseEnter={e => { e.currentTarget.style.textDecoration = 'underline'; }}
            onMouseLeave={e => { e.currentTarget.style.textDecoration = 'none'; }}
            aria-label="--login, log in"
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
