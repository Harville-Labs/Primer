import { PRIMER_CHAT_URL } from '$app/env/private';
import type { RequestHandler } from './$types';

/**
 * Proxies the chat stream from the browser to the Chat service.
 *
 * The request body is read here, because which endpoint a question belongs
 * to depends on what is in it. The response is piped through untouched: a
 * stream collected here would arrive all at once, which defeats the point of
 * streaming it.
 */

/** Matched before the id is put in a path, so nothing else can be. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const POST: RequestHandler = async ({ request, fetch }) => {
	const headers = new Headers({ 'Content-Type': 'application/json' });
	for (const name of ['x-forwarded-user', 'x-forwarded-email', 'x-forwarded-groups']) {
		const value = request.headers.get(name);
		if (value) headers.set(name, value);
	}

	const { conversation_id: conversationId, ...body } = (await request.json()) as {
		conversation_id?: string;
	} & Record<string, unknown>;

	if (conversationId !== undefined && !UUID.test(conversationId)) {
		return new Response('{"detail":"That is not a conversation id."}', {
			status: 400,
			headers: { 'Content-Type': 'application/json' },
		});
	}

	const base = PRIMER_CHAT_URL;
	// A first question opens a conversation; every one after it continues
	// that conversation, which is what lets the model see the turns before
	// it. The library is fixed when the conversation opens and is not sent
	// again - a conversation is grounded in one library or in none.
	const { library_id: _library, ...turn } = body;
	const upstream = conversationId
		? await fetch(`${base}/api/v1/conversations/${conversationId}/messages`, {
				method: 'POST',
				headers,
				body: JSON.stringify(turn),
			})
		: await fetch(`${base}/api/v1/conversations`, {
				method: 'POST',
				headers,
				body: JSON.stringify(body),
			});

	// A refusal is not a stream. Labelling one `text/event-stream` would
	// leave the browser waiting for events that are never coming, and the
	// reader would show an answer that simply never starts.
	if (!upstream.ok) {
		return new Response(upstream.body, {
			status: upstream.status,
			headers: { 'Content-Type': upstream.headers.get('content-type') ?? 'application/json' },
		});
	}

	return new Response(upstream.body, {
		status: upstream.status,
		headers: {
			'Content-Type': 'text/event-stream',
			'Cache-Control': 'no-cache',
			// Several proxies buffer by default, which would hold every token
			// until the answer finished.
			'X-Accel-Buffering': 'no',
		},
	});
};
