/**
 * What `$app/env/private` holds in a test, with the given service URLs.
 *
 * Every route that reaches a service imports the identity settings through
 * `forward.ts`, so each mock needs them. They live here once, at the values
 * a deployment gets by default, rather than in every test's `vi.mock`.
 */
export function privateEnv(urls: Record<string, string>, token = '') {
	return {
		PRIMER_SUBJECT_HEADER: 'x-forwarded-user',
		PRIMER_EMAIL_HEADER: 'x-forwarded-email',
		PRIMER_GROUPS_HEADER: 'x-forwarded-groups',
		PRIMER_WEB_TOKEN: token,
		...urls,
	};
}
