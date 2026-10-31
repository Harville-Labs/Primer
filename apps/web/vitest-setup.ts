import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/svelte';
import { afterEach } from 'vitest';

// Sivir imports Scritto for animated text. jsdom lacks constructable
// stylesheet parsing, which the module performs even when tests never render it.
CSSStyleSheet.prototype.replaceSync ??= () => {};

// Without this each test leaves its component mounted, and queries start
// matching elements from a previous test rather than this one.
afterEach(cleanup);

// jsdom has no `matchMedia`, and Svelte's `prefersReducedMotion` calls it
// when its module loads. The kit's barrel reaches that module through
// components a test never renders, so without this every test that imports
// a component fails before it starts. Nothing here asserts on a media query:
// answering "no match" is the browser's default.
if (!window.matchMedia) {
	window.matchMedia = (query: string): MediaQueryList => ({
		matches: false,
		media: query,
		onchange: null,
		addEventListener: () => {},
		removeEventListener: () => {},
		addListener: () => {},
		removeListener: () => {},
		dispatchEvent: () => false,
	});
}
