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
    },
  };
});
