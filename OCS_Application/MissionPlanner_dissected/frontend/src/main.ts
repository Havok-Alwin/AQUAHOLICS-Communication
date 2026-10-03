import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';
import { MOCK } from './lib/mode';

const target = document.getElementById('app');
if (!target) throw new Error('#app not found');

// MOCK is constant-false in a production build, so this import is dropped from the bundle.
if (MOCK) void import('./lib/mock').then((m) => m.startMock());

export default mount(App, { target });
