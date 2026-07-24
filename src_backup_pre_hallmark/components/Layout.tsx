import React from 'react';

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col relative overflow-hidden">
      {/* Grid overlay for cyberpunk feel */}
      <div className="absolute inset-0 z-0 pointer-events-none" 
        style={{
          opacity: 'var(--theme-grid-opacity, 0.2)',
          backgroundImage: 'linear-gradient(var(--accent) 1px, transparent 1px), linear-gradient(90deg, var(--accent) 1px, transparent 1px)',
          backgroundSize: '40px 40px'
        }}
      />
      <div className="relative z-10 flex flex-col flex-grow">
        {children}
      </div>
    </div>
  );
}
