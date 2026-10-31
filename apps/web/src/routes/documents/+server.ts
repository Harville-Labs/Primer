import { json } from '@sveltejs/kit';
import { ApiError } from '#lib/api/client.ts';
import type { LibrarySummary } from '#lib/api/types.ts';
import { apiFor } from '#lib/server/api.ts';
import { decodeFilename, libraryNameFor, UPLOAD_FILENAME_HEADER } from '#lib/upload.ts';
import type { RequestHandler } from './$types';

/**
 * Uploading a document, from wherever the user happens to be.
 *
 * The browser cannot reach Control directly, so this is how a file gets
 * there: the identity headers Primer trusts are set by the edge proxy, and a
 * browser that could call Control itself could set them itself.
 *
 * A library may be named or not. Chat lets you drop a file into a
 * conversation that has no library attached, and the file has to live
 * somewhere, so one is created to hold it. Creating it here rather than in
 * the browser means the create and the upload are one request: a page closed
 * midway leaves either nothing or a library with its document in it, never a
 * library the user never asked for and cannot explain.
 */
export const POST: RequestHandler = async ({ request, url, fetch }) => {
	// Read nothing from the body. Parsing the form here would hold the whole
	// file in this server's memory before Control saw a byte of it.
	const contentType = request.headers.get('content-type') ?? '';
	if (!request.body || !contentType.startsWith('multipart/form-data')) {
		return json({ error: 'No file was uploaded.' }, { status: 400 });
	}

	const api = apiFor(request, fetch);
	const libraryId = url.searchParams.get('libraryId') || null;

	try {
		// `created` is what lets the caller offer to undo this. A library the
		// user chose is not something to ask about keeping.
		let created: LibrarySummary | null = null;
		let target = libraryId;
		if (target === null) {
			const filename = decodeFilename(request.headers.get(UPLOAD_FILENAME_HEADER));
			if (filename === null) {
				return json(
					{ error: 'A file uploaded without a library needs a name to give one.' },
					{ status: 400 },
				);
			}
			created = await api.createLibrary(libraryNameFor(filename));
			target = created.id;
		}

		const document = await api.upload(target, request.body, contentType);
		return json({
			libraryId: target,
			libraryName: created?.name ?? null,
			created: created !== null,
			document,
		});
	} catch (cause) {
		if (cause instanceof ApiError) {
			return json({ error: cause.message, code: cause.code }, { status: cause.status });
		}
		throw cause;
	}
};
