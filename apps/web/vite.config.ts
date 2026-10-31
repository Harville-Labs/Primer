import adapter from '@sveltejs/adapter-node';
import { sveltekit } from '@sveltejs/kit/vite';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			preprocess: vitePreprocess(),
			// Node rather than a static build: the browser never talks to Control
			// directly. Requests go through this server, which is what lets the
			// deployment keep Control unreachable from outside the cluster.
			adapter: adapter(),
		}),
	],
	// Only under Vitest, and only there: without the browser condition Svelte
	// resolves to its server build, where mounting a component does not
	// exist. Applying it unconditionally would break server-side rendering.
	resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined,
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts'],
		setupFiles: ['./vitest-setup.ts'],
	},
});
