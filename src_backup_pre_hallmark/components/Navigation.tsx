import { Link, useLocation } from 'react-router-dom';
import { Terminal, HardDrive, PlaySquare, Sun, Moon } from 'lucide-react';
import React, { useEffect, useState } from 'react';

export function Navigation({ theme, setTheme }: { theme: string, setTheme: React.Dispatch<React.SetStateAction<string>> }) {
  const location = useLocation();

  const toggleTheme = () => {
    setTheme(prev => prev === 'dark' ? 'light' : 'dark');
  }

  return (
    <nav className="border-b-4 p-4 flex flex-col md:flex-row justify-between items-center z-50 sticky top-0" style={{ backgroundColor: 'var(--bg)', borderBottomColor: 'var(--accent)' }}>
      <div className="flex items-center gap-4 mb-4 md:mb-0">
        <button 
          onClick={toggleTheme}
          className="p-2 border transition-colors"
          style={{ color: 'var(--accent)', backgroundColor: 'var(--bg-card)', borderColor: 'var(--accent)' }}
        >
          {theme === 'light' ? <Sun size={20} /> : <Moon size={20} />}
        </button>
        <Terminal className="w-8 h-8" style={{ color: 'var(--accent-secondary)' }} />
        <Link to="/">
          <h1 
            className="font-display font-bold text-3xl tracking-widest uppercase glitch transition-colors" 
            data-text="IMAGESOUND"
            style={{ color: 'var(--text-heading)' }}
          >
            IMAGESOUND
          </h1>
        </Link>
      </div>

      <div className="flex gap-6 uppercase text-sm md:text-base font-bold items-center">
        <Link 
          to="/" 
          className="px-2 py-1 flex items-center gap-2 transition-colors"
          style={{ 
            backgroundColor: location.pathname === '/' ? 'var(--selected-bg)' : 'transparent',
            color: location.pathname === '/' ? 'var(--selected-text)' : 'var(--accent)'
          }}
        >
          <Terminal size={18} /> [ UPLOAD ]
        </Link>
        <Link 
          to="/player" 
          className="px-2 py-1 flex items-center gap-2 transition-colors"
          style={{ 
            backgroundColor: location.pathname === '/player' ? 'var(--accent-secondary)' : 'transparent',
            color: location.pathname === '/player' ? 'var(--selected-text)' : 'var(--accent-secondary)'
          }}
        >
          <PlaySquare size={18} /> [ STUDIO ]
        </Link>
        <Link 
          to="/library" 
          className="px-2 py-1 flex items-center gap-2 transition-colors"
          style={{ 
            backgroundColor: location.pathname === '/library' ? 'var(--accent-tertiary)' : 'transparent',
            color: location.pathname === '/library' ? 'var(--selected-text)' : 'var(--accent-tertiary)'
          }}
        >
          <HardDrive size={18} /> [ LIBRARY ]
        </Link>
      </div>
    </nav>
  );
}
