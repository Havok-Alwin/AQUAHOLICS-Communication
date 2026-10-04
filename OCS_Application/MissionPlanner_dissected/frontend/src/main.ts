import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';
import { startLinkB } from './lib/linkB';
import { startLinkC } from './lib/linkC';
import { MOCK } from './lib/mode';

const target = document.getElementById('app');
if (!target) throw new Error('#app not found');

// MOCK is constant-false in a production build, so this import is dropped from the bundle.
if (MOCK) void import('./lib/mock').then((m) => m.startMock());
else {
  startLinkB();
  startLinkC();
}

export default mount(App, { target });
