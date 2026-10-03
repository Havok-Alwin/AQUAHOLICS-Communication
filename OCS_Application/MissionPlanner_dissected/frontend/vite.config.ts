import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  build: {
    // Everything is bundled locally: the course network has no Internet.
    outDir: 'dist',
    assetsInlineLimit: 0,
  },
});
