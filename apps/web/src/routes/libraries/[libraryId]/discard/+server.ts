import { json } from '@sveltejs/kit';
import { ApiError } from '#lib/api/client.ts';
import { apiFor } from '#lib/server/api.ts';
import type { RequestHandler } from './$types';

/**
 * Throwing away a library that was created to hold a dropped file.
 *
 * Only reachable for a library the caller owns, because Control decides that
 * and this forwards the caller's identity rather than asserting one. It is a
 * plain delete: there is no separate "discard" concept in Control, and
 * inventing one would mean a second path to removing a library that could
 * disagree with the first about who may do it.
 */
export const POST: RequestHandler = async ({ params, request, fetch }) => {
	try {
		await apiFor(request, fetch).deleteLibrary(params.libraryId);
	} catch (cause) {
		if (cause instanceof ApiError) {
			return json({ error: cause.message }, { status: cause.status });
		}
		throw cause;
	}
	return json({ discarded: true });
};
