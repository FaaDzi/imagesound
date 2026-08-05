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
        '^/api/': {
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
      // Vite's dev server otherwise serves the ENTIRE project root as static
      // files, unauthenticated -- with the frontend tunneled to the public
      // internet (see run.py's --tunnel flag), that means anyone with the
      // URL could fetch backend/app.db (the SQLite DB, incl. the bcrypt
      // password hash), backend/storage/converted/<id>.wav (any generated
      // song, bypassing the backend's own GET /audio/{id} auth gate
      // entirely), backend/app/config.py, run.py, etc. This denylist blocks
      // Vite's static file server from ever serving those paths, regardless
      // of tunnel vs. local-only use (the same request works on plain
      // localhost:3000 today, tunneling just makes it internet-reachable).
      fs: {
        deny: [
          // Vite's own defaults -- MUST stay here since setting `deny` at all
          // replaces (does not merge with) Vite's built-in denylist.
          '.env',
          '.env.*',
          '*.{crt,pem}',
          '**/.git/**',
          // project-specific
          '**/backend/**',
          '**/pipeline/**',
          '**/.venv/**',
          '**/.venv-fad/**',
          '**/*.py',
          '**/*.db',
          '**/*.db-wal',
          '**/*.db-shm',
          '**/*.db-journal',
          '**/*.pid',
          '**/*.log',
          '**/*.csv',
          '**/.superpowers/**',
          '**/docs/**',
          '**/.claude/**',
          '**/.vscode/**',
          '**/.hallmark/**',
          'package-lock.json',
          '**/.fad_eval/**',
          // Anchored to the project's OWN top-level dist/ (build output) --
          // NOT '**/dist/**', which also matches node_modules/vite/dist/**
          // and every other dependency's own dist folder, silently blocking
          // Vite from serving its own client runtime (node_modules/vite/dist/
          // client/client.mjs, i.e. the /@vite/client script every page
          // loads) and breaking the app with a blank white screen -- this
          // was a real bug, not hypothetical, caught by an actual blank page.
          `${path.resolve(__dirname, 'dist')}/**`,
        ],
      },
    },
  };
});
