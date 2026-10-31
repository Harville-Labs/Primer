import { error, json } from '@sveltejs/kit';
import { ApiError } from '#lib/api/client.ts';
import { chatFor } from '#lib/server/chat.ts';
import type { RequestHandler } from './$types';

/**
 * Deleting one conversation.
 *
 * Through this server rather than from the browser, for the reason every
 * other call is: the identity Chat acts on comes from headers the proxy
 * sets, and a browser that could call Chat directly could set them itself.
 */
export const DELETE: RequestHandler = async ({ request, fetch, params }) => {
	try {
		await chatFor(request, fetch).deleteConversation(params.id);
	} catch (failure) {
		// Chat's own status, so a conversation that is not the caller's reads
		// as missing here too rather than becoming a generic failure.
		if (failure instanceof ApiError) error(failure.status, failure.message);
		throw failure;
	}
	return json({ deleted: params.id });
};
