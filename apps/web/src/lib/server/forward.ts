import {
	PRIMER_EMAIL_HEADER,
	PRIMER_GROUPS_HEADER,
	PRIMER_SUBJECT_HEADER,
	PRIMER_WEB_TOKEN,
} from '$app/env/private';

/** Must match `WEB_TOKEN_HEADER` in `primer_service.credentials`. */
const WEB_TOKEN_HEADER = 'x-primer-web-token';

/**
 * A `fetch` that carries the caller's identity to a Primer service.
 *
 * Every service trusts the identity headers the edge proxy set, and the
 * browser never reaches a service itself: if it could, it could set those
 * headers itself and be anyone. So each request this server makes on a
 * user's behalf copies exactly these headers from the request it is
 * answering, and nothing else, and adds the credential that tells the
 * service this server forwarded them. Without it a service believes no
 * identity, since anything else inside the cluster could name anyone.
 */
export function forwardingIdentity(
	request: Request,
	fetch: typeof globalThis.fetch,
): typeof globalThis.fetch {
	const forwarded = new Headers();
	for (const name of [
		PRIMER_SUBJECT_HEADER,
		PRIMER_EMAIL_HEADER,
		PRIMER_GROUPS_HEADER,
		'x-request-id',
	]) {
		const value = request.headers.get(name);
		if (value) forwarded.set(name, value);
	}
	if (PRIMER_WEB_TOKEN) forwarded.set(WEB_TOKEN_HEADER, PRIMER_WEB_TOKEN);

	return (input, init = {}) => {
		const headers = new Headers(init.headers);
		for (const [name, value] of forwarded) headers.set(name, value);
		return fetch(input, { ...init, headers });
	};
}
