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

/**
 * A header the proxy sets, under the name Control, Chat and Lab read it by.
 *
 * The same variable configures both sides, so a deployment that renames one
 * renames it everywhere. Lowercased, because that is how `Headers` holds names.
 */
function headerName(fallback: string) {
	return (value: string | undefined): string => (value || fallback).toLowerCase();
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
	PRIMER_LAB_URL: {
		description: 'Where Lab listens, when this deployment has one. Control says whether it does.',
		schema: serviceUrl('PRIMER_LAB_URL', 'http://localhost:8200'),
	},
	PRIMER_SUBJECT_HEADER: {
		description: 'The header the proxy puts the signed-in subject in.',
		schema: headerName('X-Forwarded-User'),
	},
	PRIMER_EMAIL_HEADER: {
		description: "The header the proxy puts the user's email in.",
		schema: headerName('X-Forwarded-Email'),
	},
	PRIMER_GROUPS_HEADER: {
		description: "The header the proxy puts the user's groups in.",
		schema: headerName('X-Forwarded-Groups'),
	},
	PRIMER_WEB_TOKEN: {
		description:
			'What this server presents with the identity it forwards. Required when authentication is on.',
		schema: (value: string | undefined): string => value ?? '',
	},
});
