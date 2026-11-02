import { error } from '@sveltejs/kit';
import { studioRelay } from '#lib/server/studio.ts';
import type { RequestHandler } from './$types';

/** A page of the caller's history, older than `before` when given. */
export const GET: RequestHandler = (event) =>
	studioRelay(event, (control) =>
		control.studioImages(event.url.searchParams.get('before') ?? undefined),
	);

/**
 * An image to edit, from the person's own files.
 *
 * Passed on unread, as a document upload is: Control checks the size
 * before it decodes anything, and re-encodes what it keeps.
 */
export const POST: RequestHandler = (event) =>
	studioRelay(event, (control) => {
		const contentType = event.request.headers.get('content-type') ?? '';
		const body = event.request.body;
		if (!body || !contentType.startsWith('multipart/form-data')) {
			error(400, 'No image was uploaded.');
		}
		return control.uploadStudioImage(body, contentType);
	});
