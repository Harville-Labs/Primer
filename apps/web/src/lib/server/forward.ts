/**
 * A `fetch` that carries the caller's identity to a Primer service.
 *
 * Every service trusts the identity headers the edge proxy set, and the
 * browser never reaches a service itself: if it could, it could set those
 * headers itself and be anyone. So each request this server makes on a
 * user's behalf copies exactly these headers from the request it is
 * answering, and nothing else.
 */
export function forwardingIdentity(
	request: Request,
	fetch: typeof globalThis.fetch,
): typeof globalThis.fetch {
	const forwarded = new Headers();
	for (const name of [
		'x-forwarded-user',
		'x-forwarded-email',
		'x-forwarded-groups',
		'x-request-id',
	]) {
		const value = request.headers.get(name);
		if (value) forwarded.set(name, value);
	}

	return (input, init = {}) => {
		const headers = new Headers(init.headers);
		for (const [name, value] of forwarded) headers.set(name, value);
		return fetch(input, { ...init, headers });
	};
}
