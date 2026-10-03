import { readable } from 'svelte/store';
import { CLOCK_TICK_MS } from './pacing';

// Wall clock for staleness and age display. Not the HUD frame clock:
// the HUD draws on its own requestAnimationFrame loop (logic 0).
export const now = readable(Date.now(), (set) => {
  const id = setInterval(() => set(Date.now()), CLOCK_TICK_MS);
  return () => clearInterval(id);
});
