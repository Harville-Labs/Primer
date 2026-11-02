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
});
