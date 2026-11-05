import type { DocumentSummary, GraphStatus } from './api/types';

/**
 * What the library page's graph card reads: the counts, or why there are
 * none. A failure is carried as a message rather than thrown, because the
 * graph being down is not a reason to fail the document list beside it.
 */
export type GraphReading = { status: GraphStatus } | { error: string };

/**
 * Searchable documents the knowledge graph has nothing for.
 *
 * Only ready documents: one still being indexed has no graph yet because it
 * has no index yet, and listing it would ask for a reindex of something
 * already on its way. Compared by current version, since a graph belongs to
 * a version and an older version's graph says nothing about this one.
 */
export function withoutGraph(
	documents: readonly DocumentSummary[],
	status: GraphStatus,
): DocumentSummary[] {
	const graphed = new Set(status.graphed_versions);
	return documents.filter(
		(document) => document.status === 'ready' && !graphed.has(document.current_version_id),
	);
}
