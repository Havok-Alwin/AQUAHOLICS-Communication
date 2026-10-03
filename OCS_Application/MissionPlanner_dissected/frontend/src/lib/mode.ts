// Mock mode exists only on the dev server (`npm run dev:mock`).
// A production build is always real mode, even if built with `--mode mock`,
// so simulated data can never reach a real deployment.
export const MOCK: boolean = import.meta.env.DEV && import.meta.env.MODE === 'mock';
