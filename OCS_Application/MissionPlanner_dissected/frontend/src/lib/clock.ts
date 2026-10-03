import { readable } from 'svelte/store';

// Wall clock for staleness and age display. Not the HUD frame clock:
// the HUD draws on its own requestAnimationFrame loop (logic 0).
export const now = readable(Date.now(), (set) => {
  const id = setInterval(() => set(Date.now()), 250);
  return () => clearInterval(id);
});
