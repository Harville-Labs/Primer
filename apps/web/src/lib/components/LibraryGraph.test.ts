import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { DocumentSummary, GraphStatus } from '#lib/api/types.ts';
import type { GraphReading } from '#lib/graph-status.ts';
import LibraryGraph from './LibraryGraph.svelte';

function document(id: string): DocumentSummary {
	return {
		id,
		current_version_id: `${id}-v1`,
		filename: `${id}.pdf`,
		status: 'ready',
	} as DocumentSummary;
}

function status(overrides: Partial<GraphStatus> = {}): Promise<GraphReading> {
	return Promise.resolve({
		status: {
			entities: 1200,
			relations: 340,
			communities: 9,
			graphed_versions: ['a-v1'],
			...overrides,
		},
	});
}

describe('the knowledge graph card', () => {
	it('counts the graph and names the documents it does not cover', async () => {
		render(LibraryGraph, {
			reading: status(),
			documents: [document('a'), document('b')],
			owned: true,
		});

		expect(await screen.findByText('1,200')).toBeTruthy();
		expect(screen.getByText('340')).toBeTruthy();
		expect(screen.getByText('9')).toBeTruthy();
		expect(screen.getByText('1 document has no graph')).toBeTruthy();
		expect(screen.getByText('b.pdf')).toBeTruthy();
		expect(screen.queryByText('a.pdf')).toBeNull();
		expect(screen.getByText(/Reindex once an extraction model/)).toBeTruthy();
	});

	it('tells a reader who can reindex, rather than asking them to', async () => {
		render(LibraryGraph, { reading: status(), documents: [document('b')], owned: false });

		expect(await screen.findByText(/owner can reindex them/)).toBeTruthy();
	});

	it('says why library-wide questions are unavailable when the library is too large to group', async () => {
		render(LibraryGraph, {
			reading: status({ communities: null }),
			documents: [document('a')],
			owned: true,
		});

		expect(await screen.findByText(/too large to group into topics/)).toBeTruthy();
		expect(screen.queryByText(/no graph/)).toBeNull();
	});

	it('shows why the graph could not be read instead of counts', async () => {
		const reading = Promise.resolve({ error: 'The knowledge graph is unreachable.' });

		render(LibraryGraph, { reading, documents: [document('a')], owned: true });

		expect(await screen.findByText('The knowledge graph is unreachable.')).toBeTruthy();
		expect(screen.queryByText('Entities')).toBeNull();
	});
});
