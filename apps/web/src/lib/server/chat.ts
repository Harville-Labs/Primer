import { ChatApi } from '#lib/api/chat-client.ts';
import { PRIMER_CHAT_URL } from '$app/env/private';

/**
 * The server's connection to Chat.
 *
 * The same rule as Control: the browser never reaches Chat directly. The
 * identity headers Primer trusts are set by the proxy in front of it, and a
 * browser that could call Chat itself could set them itself and be anyone.
 */
export function chatFor(request: Request, fetch: typeof globalThis.fetch): ChatApi {
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

	return new ChatApi({
		baseUrl: PRIMER_CHAT_URL,
		fetch: (input, init = {}) => {
			const headers = new Headers(init.headers);
			for (const [name, value] of forwarded) headers.set(name, value);
			return fetch(input, { ...init, headers });
		},
	});
}
