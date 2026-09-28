import React from 'react';

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col">
      {children}

      {/* Soft status-strip footer — see docs/superpowers/specs/2026-08-04-y2k-aqua-redesign-design.md § Footer */}
      <footer
        className="px-4 md:px-8 py-3 text-[11px] uppercase tracking-widest"
        style={{ borderTop: '1px solid var(--border)', color: 'var(--text-muted)' }}
      >
        imagesound · pictures, songs and words into music
      </footer>
    </div>
  );
}
