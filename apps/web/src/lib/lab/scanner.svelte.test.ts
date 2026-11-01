import { describe, expect, it } from 'vitest';
import type { LabScanRequest, ScannedPassage } from '#lib/api/types.ts';
import { PROBE, passage, RESIDENT } from './fixture';
import { Scanner } from './scanner.svelte';

const LIBRARY = { id: 'lib/../1', name: 'Papers' };

/**
 * The page's own passage and scan routes, keeping every request. Each
 * passage's surprisal is the number in its id, so the ranking can be read
 * off the ids; `onscan` runs before each batch is answered.
 */
function primer(count: number, options: { onscan?: () => void; listing?: () => Response } = {}) {
	const asked: string[] = [];
	const sent: LabScanRequest[] = [];
	const passages = Array.from({ length: count }, (_, index) => passage(String(index)));
	const fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
		const key = `${init.method ?? 'GET'} ${String(input)}`;
		asked.push(key);
		if (key.startsWith('GET /lab/passages/')) {
			return options.listing?.() ?? Response.json({ passages, total: count + 5 });
		}
		if (key === 'POST /lab/scan/batch') {
			const body = JSON.parse(String(init.body)) as LabScanRequest;
			sent.push(body);
			options.onscan?.();
			const scored: ScannedPassage[] = body.passages.map((each) => ({
				id: each.id,
				tokens: 3,
				truncated: false,
				mean_bits: Number(each.id),
				peak_bits: Number(each.id),
				probe: body.probe_id ? 1 / (1 + Number(each.id)) : null,
			}));
			return Response.json({
				model: RESIDENT,
				probe: body.probe_id ? PROBE : null,
				passages: scored,
			});
		}
		throw new Error(`nothing answers ${key}`);
	}) as typeof globalThis.fetch;
	return { fetch, asked, sent };
}

describe('scanning a library', () => {
	it('lists the library once, then sends Lab its passages in batches of at most 16', async () => {
		const { fetch, asked, sent } = primer(40);
		const scanner = new Scanner({ fetch });

		await scanner.start(LIBRARY, null);

		expect(asked[0]).toBe(`GET /lab/passages/${encodeURIComponent(LIBRARY.id)}`);
		expect(sent.map((each) => each.passages.length)).toEqual([16, 16, 8]);
		expect(sent.flatMap((each) => each.passages.map((p) => p.id))).toHaveLength(40);
		expect(scanner.phase).toBe('done');
		expect([scanner.scanned, scanner.listed, scanner.total]).toEqual([40, 40, 45]);
	});

	it('keeps the most surprising fifty, most surprising first, across batches', async () => {
		const { fetch } = primer(70);
		const scanner = new Scanner({ fetch });

		await scanner.start(LIBRARY, null);

		const kept = scanner.findings.map((each) => Number(each.passage.chunk_id));
		expect(kept).toHaveLength(50);
		expect(kept[0]).toBe(69);
		expect(kept.at(-1)).toBe(20);
	});

	it('ranks by the probe when one was chosen, and says which', async () => {
		const { fetch, sent } = primer(20);
		const scanner = new Scanner({ fetch });

		await scanner.start(LIBRARY, PROBE.id);

		expect(sent.every((each) => each.probe_id === PROBE.id)).toBe(true);
		// The probe reads highest where surprisal is lowest, in this fake.
		expect(scanner.findings[0].passage.chunk_id).toBe('0');
		expect(scanner.probe?.id).toBe(PROBE.id);
	});

	it('sends no more batches once stopped', async () => {
		let scanner: Scanner | null = null;
		const { fetch, sent } = primer(64, {
			onscan: () => {
				if (sent.length === 2) scanner?.stop();
			},
		});
		scanner = new Scanner({ fetch });

		await scanner.start(LIBRARY, null);

		expect(sent).toHaveLength(2);
		expect(scanner.phase).toBe('stopped');
		expect(scanner.failure).toBeNull();
	});

	it('says why a library could not be listed, in Primer’s words, and scans nothing', async () => {
		const { fetch, sent } = primer(4, {
			listing: () =>
				Response.json(
					{ code: 'not_found', title: 'Library not found', status: 404, detail: 'No library.' },
					{ status: 404 },
				),
		});
		const scanner = new Scanner({ fetch });

		await scanner.start(LIBRARY, null);

		expect(sent).toEqual([]);
		expect(scanner.failure).toEqual({ title: 'Library not found', detail: 'No library.' });
		expect(scanner.run()).toBeNull();
	});

	it('saves what it found and shows a saved scan without scoring again', async () => {
		const { fetch, asked } = primer(3);
		const scanner = new Scanner({ fetch });
		await scanner.start(LIBRARY, null);
		const run = scanner.run();
		if (!run) throw new Error('a finished scan has a run to save');
		expect(run.result?.findings).toHaveLength(3);

		const reopened = new Scanner({ fetch });
		const before = asked.length;
		reopened.show(run);

		expect(asked).toHaveLength(before);
		expect(reopened.findings).toEqual(run.result?.findings);
		expect(reopened.library).toEqual(LIBRARY);
		expect(reopened.phase).toBe('done');
	});
});
