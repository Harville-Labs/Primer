/**
 * Uploading from the browser.
 *
 * Everything goes through this server rather than to Control directly. The
 * identity headers Primer trusts are set by the edge proxy in front of it,
 * and a browser that could call Control itself could set them itself and be
 * anyone.
 */

import type { DocumentSummary } from '#lib/api/types.ts';

export interface UploadResult {
	libraryId: string;
	/** Set only when a library was created to hold this file. */
	libraryName: string | null;
	created: boolean;
	document: DocumentSummary;
}

async function failureFrom(response: Response, fallback: string): Promise<Error> {
	try {
		const body = (await response.json()) as { error?: string };
		return new Error(body.error ?? fallback);
	} catch {
		// A proxy timeout or a crash produces a non-JSON body. The status is
		// still worth reporting; a parse error is not.
		return new Error(`${fallback} (${response.status})`);
	}
}

/**
 * Send one file. Without a library, one is created to hold it and named
 * after the file, and `created` says so — which is what lets a caller offer
 * to undo it.
 */
export async function uploadDocument(file: File, libraryId?: string): Promise<UploadResult> {
	const body = new FormData();
	body.append('file', file);
	if (libraryId) body.append('libraryId', libraryId);

	const response = await fetch('/documents', { method: 'POST', body });
	if (!response.ok) {
		throw await failureFrom(response, `${file.name} could not be uploaded.`);
	}
	return (await response.json()) as UploadResult;
}

/** Throw away a library that was created only to hold a dropped file. */
export async function discardLibrary(libraryId: string): Promise<void> {
	const response = await fetch(`/libraries/${libraryId}/discard`, { method: 'POST' });
	if (!response.ok) {
		throw await failureFrom(response, 'That library could not be removed.');
	}
}
