import { ChatApi } from '#lib/api/chat-client.ts';
import { forwardingIdentity } from '#lib/server/forward.ts';
import { PRIMER_CHAT_URL } from '$app/env/private';

/** The server's connection to Chat, as the user the request is from. */
export function chatFor(request: Request, fetch: typeof globalThis.fetch): ChatApi {
	return new ChatApi({ baseUrl: PRIMER_CHAT_URL, fetch: forwardingIdentity(request, fetch) });
}
