import { defineEnvVars } from '@sveltejs/kit/env';

/**
 * A service address, defaulting to where `pnpm dev` expects it.
 *
 * Checked when the server starts, so a typo in a deployment fails there with
 * the variable's name rather than on the first request as a fetch error that
 * names neither.
 */
function serviceUrl(name: string, fallback: string) {
	return (value: string | undefined): string => {
		const url = value || fallback;
		const protocol = URL.parse(url)?.protocol;
		if (protocol !== 'http:' && protocol !== 'https:') {
			throw new Error(`${name} must be an http:// or https:// URL, got ${JSON.stringify(url)}.`);
		}
		return url;
	};
}

export const variables = defineEnvVars({
	PRIMER_CONTROL_URL: {
		description: 'Where Control listens. The browser never reaches it; this server does.',
		schema: serviceUrl('PRIMER_CONTROL_URL', 'http://localhost:8000'),
	},
	PRIMER_CHAT_URL: {
		description: 'Where Chat listens. Answers stream through this server.',
		schema: serviceUrl('PRIMER_CHAT_URL', 'http://localhost:8100'),
	},
});
