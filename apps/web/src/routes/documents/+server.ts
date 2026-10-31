import { json } from '@sveltejs/kit';
import { ApiError } from '#lib/api/client.ts';
import type { LibrarySummary } from '#lib/api/types.ts';
import { apiFor } from '#lib/server/api.ts';
import { libraryNameFor } from '#lib/upload.ts';
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
export const POST: RequestHandler = async ({ request, fetch }) => {
	const form = await request.formData();
	const file = form.get('file');
	if (!(file instanceof File)) {
		return json({ error: 'No file was uploaded.' }, { status: 400 });
	}

	const api = apiFor(request, fetch);
	const existing = form.get('libraryId');
	const libraryId = typeof existing === 'string' && existing ? existing : null;

	try {
		// `created` is what lets the caller offer to undo this. A library the
		// user chose is not something to ask about keeping.
		let created: LibrarySummary | null = null;
		let target = libraryId;
		if (target === null) {
			created = await api.createLibrary(libraryNameFor(file.name));
			target = created.id;
		}

		const document = await api.upload(target, file);
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
