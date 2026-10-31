import { error } from '@sveltejs/kit';
import { ApiError } from '#lib/api/client.ts';
import { downloadHeaders } from '#lib/download.ts';
import { apiFor } from '#lib/server/api.ts';
import type { RequestHandler } from './$types';

/**
 * Handing back a document's own bytes.
 *
 * The document table has always linked filenames here, and nothing
 * answered: there was no route, so clicking a document in a library you
 * own produced SvelteKit's own bare "404 Not Found" - not Primer's, which
 * is why it looked like the app had lost the file rather than refused it.
 *
 * A proxy, like every other call: the browser cannot reach Control
 * directly, because the identity headers Primer trusts are set by the edge
 * and a browser that could call Control could set them itself.
 */
export const GET: RequestHandler = async ({ params, request, fetch }) => {
	let upstream: Response;
	try {
		upstream = await apiFor(request, fetch).content(params.libraryId, params.documentId);
	} catch (cause) {
		// Primer's own 404 page, with Control's wording. The alternative is
		// the framework's, which tells a reader nothing about whether the
		// document is gone or simply not theirs.
		if (cause instanceof ApiError) error(cause.status, cause.message);
		throw cause;
	}

	// Streamed through, not buffered: a hundred-megabyte upload should not
	// have to fit in this process to be downloaded.
	return new Response(upstream.body, {
		headers: downloadHeaders(upstream.headers),
	});
};
