import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      // Set DISABLE_HMR=true in the environment to turn off hot module
      // reloading and file watching below — useful when an automated tool
      // (e.g. a coding agent) is making bulk edits and you do not want the
      // dev server flickering/reloading mid-edit.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      // Watch the frontend only. Without these excludes, every DB read/write
      // (backend runs SQLite in WAL mode, so even a plain SELECT touches
      // app.db-wal/-shm) and every generated audio file gets picked up as a
      // "source change" and forces a full page reload mid-action (playing,
      // saving, or generating a song).
      watch: process.env.DISABLE_HMR === 'true' ? null : {
        ignored: [
          '**/backend/**',
          '**/pipeline/**',
          '**/.venv/**',
          '**/.venv-fad/**',
          '**/graphify-out/**',
          '**/__pycache__/**',
          '**/*.db',
          '**/*.db-wal',
          '**/*.db-shm',
          '**/*.db-journal',
        ],
      },
      // Lets a single tunneled origin (see run.py's --tunnel flag) reach both
      // the frontend and the backend through one URL: any request to /api/*
      // is forwarded to the backend with the /api prefix stripped, so the
      // browser never needs to know about localhost:8000 directly. Local
      // dev without --tunnel is unaffected -- src/api.ts talks to
      // http://localhost:8000 directly unless VITE_API_BASE overrides it.
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
      // Vite 6's dev server rejects any request whose Host header isn't
      // localhost or explicitly allowlisted (DNS-rebinding protection) --
      // without this, cloudflared's random *.trycloudflare.com hostname
      // (see run.py's --tunnel flag) gets a 403 "Blocked request" for every
      // request. A leading "." allows any subdomain, covering the random
      // quick-tunnel hostname without needing to know it in advance. Safe to
      // leave enabled unconditionally -- it only relaxes the host check for
      // *.trycloudflare.com and has no effect otherwise.
      allowedHosts: ['.trycloudflare.com'],
    },
  };
});
