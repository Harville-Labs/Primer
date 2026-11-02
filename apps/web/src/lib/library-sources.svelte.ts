import type { DocumentSummary } from '#lib/api/types.ts';

/**
 * The documents a conversation's citations point into, by id.
 *
 * A citation carries ids and an excerpt, not a filename or a media type:
 * those belong to the document, which can be renamed or replaced after
 * the answer was written. So the panel asks for them when it opens, and an
 * image's citation can show the image.
 */
export class LibrarySources {
	// Raw: replaced whole, never edited in place.
	byId = $state.raw(new Map<string, DocumentSummary>());
	private wanted = '';
	private readonly fetch: typeof globalThis.fetch;

	constructor(fetch?: typeof globalThis.fetch) {
		this.fetch = fetch ?? globalThis.fetch.bind(globalThis);
	}

	async load(libraryId: string): Promise<void> {
		this.wanted = libraryId;
		if (!libraryId) {
			this.byId = new Map();
			return;
		}
		let documents: DocumentSummary[];
		try {
			const response = await this.fetch(`/libraries/${libraryId}/documents`);
			// Names and thumbnails are a nicety: a citation still shows its
			// passage without them, so a failure here leaves what was there.
			if (!response.ok) return;
			documents = (await response.json()) as DocumentSummary[];
		} catch {
			return;
		}
		// Another library was asked for while this one was on its way.
		if (this.wanted !== libraryId) return;
		this.byId = new Map(documents.map((document) => [document.id, document]));
	}
}
