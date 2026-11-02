import { error, json } from '@sveltejs/kit';
import { ApiError } from '#lib/api/client.ts';
import { apiFor } from '#lib/server/api.ts';
import type { RequestHandler } from './$types';

/** A library's documents, for the chat page to name and show what it cites. */
export const GET: RequestHandler = async ({ params, request, fetch }) => {
	try {
		return json(await apiFor(request, fetch).documents(params.libraryId));
	} catch (cause) {
		if (cause instanceof ApiError) error(cause.status, cause.message);
		throw cause;
	}
};
