import { describe, expect, it } from 'vitest';
import type { DocumentSummary } from '#lib/api/types.ts';
import { LibrarySources } from './library-sources.svelte';

function document(id: string, media_type = 'application/pdf'): DocumentSummary {
	return { id, filename: `${id}.pdf`, media_type, library_id: 'lib' } as DocumentSummary;
}

describe('the documents behind a conversation’s citations', () => {
	it('are loaded by id for the library asked for', async () => {
		const asked: string[] = [];
		const sources = new LibrarySources((async (input: RequestInfo | URL) => {
			asked.push(String(input));
			return Response.json([document('a'), document('b', 'image/png')]);
		}) as typeof globalThis.fetch);

		await sources.load('lib');

		expect(asked).toEqual(['/libraries/lib/documents']);
		expect(sources.byId.get('b')?.media_type).toBe('image/png');
	});

	it('keep the library picked last when an earlier answer arrives late', async () => {
		let releaseFirst = () => {};
		const sources = new LibrarySources((async (input: RequestInfo | URL) => {
			if (String(input).includes('first')) {
				await new Promise<void>((resolve) => {
					releaseFirst = resolve;
				});
				return Response.json([document('stale')]);
			}
			return Response.json([document('fresh')]);
		}) as typeof globalThis.fetch);

		const first = sources.load('first');
		await sources.load('second');
		releaseFirst();
		await first;

		expect([...sources.byId.keys()]).toEqual(['fresh']);
	});

	it('are left as they were when Control will not answer', async () => {
		const answers = [Response.json([document('a')]), new Response(null, { status: 502 })];
		const sources = new LibrarySources((async () => answers.shift()) as typeof globalThis.fetch);

		await sources.load('lib');
		await sources.load('lib');

		expect([...sources.byId.keys()]).toEqual(['a']);
	});

	it('are none without a library', async () => {
		const sources = new LibrarySources((async () => {
			throw new Error('nothing should be asked');
		}) as typeof globalThis.fetch);

		await sources.load('');

		expect(sources.byId.size).toBe(0);
	});
});
