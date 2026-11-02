import type { Passage, ScanFinding, ScannedPassage } from '#lib/api/types.ts';

/**
 * Deciding what a scan shows: the order of its findings, how a library is
 * cut into batches, and where a finding links. Pure, so the ranking a
 * reader trusts is the one the tests check.
 */

/** The most passages a scan lists, matching Retrieval's ceiling. */
export const LISTING = 2000;

/** Passages per request to Lab, matching Lab's ceiling. */
export const BATCH = 16;

/** The most findings kept on screen and in a saved scan, matching Lab's ceiling. */
export const KEPT = 50;

/**
 * Most anomalous first: by the probe when one was read, since a probe was
 * trained for exactly this question, and by mean surprisal otherwise. A
 * passage with no reading at all sorts last, rather than as a zero that
 * would look like a clean bill of health.
 */
export function rank(findings: readonly ScanFinding[], byProbe: boolean): ScanFinding[] {
	const key = (scored: ScannedPassage) => (byProbe ? scored.probe : scored.mean_bits);
	return [...findings].sort((a, b) => {
		const left = key(a.scored);
		const right = key(b.scored);
		if (left === null) return right === null ? 0 : 1;
		if (right === null) return -1;
		return right - left;
	});
}

export function batches<T>(items: readonly T[], size = BATCH): T[][] {
	const cut: T[][] = [];
	for (let start = 0; start < items.length; start += size) {
		cut.push(items.slice(start, start + size));
	}
	return cut;
}

/** The document a passage came from, at its page when it has one. */
export function passageLink(libraryId: string, passage: Passage): string {
	const document = `/libraries/${encodeURIComponent(libraryId)}/documents/${encodeURIComponent(passage.document_id)}/content`;
	const page = passage.locator?.page;
	return page ? `${document}#page=${page}` : document;
}
