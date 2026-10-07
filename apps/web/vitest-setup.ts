import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/svelte';
import { afterEach } from 'vitest';

// Sivir imports Scritto for animated text. jsdom lacks constructable
// stylesheet parsing, which the module performs even when tests never render it.
CSSStyleSheet.prototype.replaceSync ??= () => {};

// Without this each test leaves its component mounted, and queries start
// matching elements from a previous test rather than this one.
afterEach(cleanup);
