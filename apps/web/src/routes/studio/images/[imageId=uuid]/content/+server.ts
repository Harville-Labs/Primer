import { downloadHeaders } from '#lib/download.ts';
import { studioAnswer } from '#lib/server/studio.ts';
import type { RequestHandler } from './$types';

/**
 * An image's bytes, for the grid and for download.
 *
 * Control's `attachment` disposition is kept: an `<img>` ignores it, and a
 * link straight to this saves the file rather than opening it in Primer's
 * origin. Always a PNG Control re-encoded, but the rule for bytes people
 * supplied is the same everywhere, and uploads are bytes people supplied.
 */
export const GET: RequestHandler = (event) =>
	studioAnswer(event, async (control) => {
		const upstream = await control.studioContent(event.params.imageId);
		return new Response(upstream.body, { headers: downloadHeaders(upstream.headers) });
	});
