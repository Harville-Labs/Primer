import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { Citation, DocumentSummary } from '#lib/api/types.ts';
import CitationPanel from './CitationPanel.svelte';

function cite(documentId: string): Citation {
	return {
		document_id: documentId,
		document_version_id: `${documentId}-v1`,
		chunk_id: `${documentId}-c1`,
		excerpt: 'The image shows: a heron.',
		graph_entities: [],
	};
}

function source(id: string, filename: string, media_type: string): DocumentSummary {
	return { id, filename, media_type, library_id: 'lib' } as DocumentSummary;
}

describe('the sources panel', () => {
	it('shows a cited image as a thumbnail, and a cited text by name alone', () => {
		const documents = new Map([
			['img', source('img', 'primer-heron.png', 'image/png')],
			['pdf', source('pdf', 'birds.pdf', 'application/pdf')],
		]);

		render(CitationPanel, { citations: [cite('img'), cite('pdf')], documents, open: true });

		const images = screen.getAllByRole('img');
		expect(images).toHaveLength(1);
		expect(images[0].getAttribute('src')).toBe('/libraries/lib/documents/img/content');
		expect(screen.getByText(/birds\.pdf/)).toBeTruthy();
	});

	it('marks a passage the knowledge graph found, and names what connected it', () => {
		const citations: Citation[] = [
			{ ...cite('graph'), found_by: 'graph', graph_entities: ['heron', 'wetland'] },
			{ ...cite('both'), found_by: 'both' },
			{ ...cite('vector'), found_by: 'vector' },
			// Stored before provenance was recorded: no claim either way.
			cite('older'),
		];

		render(CitationPanel, { citations, open: true });

		expect(screen.getAllByText('via graph')).toHaveLength(2);
		expect(screen.getAllByText(/Connected through/)).toHaveLength(1);
		expect(screen.getByText('Connected through: heron, wetland')).toBeTruthy();
	});
});
