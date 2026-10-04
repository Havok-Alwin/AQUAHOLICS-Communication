import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  server: {
    // Dev only: link B goes to the vehicle backend, so the page uses the same /linkb URL as in
    // deployment (where the backend serves the page itself).
    proxy: {
      '/linkb': { target: 'ws://127.0.0.1:5080', ws: true },
    },
  },
  build: {
    // Everything is bundled locally: the course network has no Internet.
    outDir: 'dist',
    assetsInlineLimit: 0,
  },
});
