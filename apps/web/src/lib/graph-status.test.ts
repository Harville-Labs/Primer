import { describe, expect, it } from 'vitest';
import type { DocumentSummary, GraphStatus } from './api/types';
import { withoutGraph } from './graph-status';

function document(id: string, status: DocumentSummary['status']): DocumentSummary {
	return { id, current_version_id: `${id}-v2`, filename: `${id}.pdf`, status } as DocumentSummary;
}

function graphed(...versions: string[]): GraphStatus {
	return { entities: 1, relations: 0, communities: 1, graphed_versions: versions };
}

describe('documents without a graph', () => {
	it('lists a ready document whose current version was never extracted', () => {
		const documents = [document('a', 'ready'), document('b', 'ready')];

		expect(withoutGraph(documents, graphed('a-v2')).map((d) => d.id)).toEqual(['b']);
	});

	it('does not count a graph an older version had', () => {
		expect(withoutGraph([document('a', 'ready')], graphed('a-v1'))).toHaveLength(1);
	});

	it('leaves out a document that is still being indexed, or failed', () => {
		const documents = [document('a', 'embedding'), document('b', 'failed')];

		expect(withoutGraph(documents, graphed())).toEqual([]);
	});
});
