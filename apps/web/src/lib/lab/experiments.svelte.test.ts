import { describe, expect, it } from 'vitest';
import type { ExperimentSummary, NeuronsRun } from '#lib/api/types.ts';
import { Experiments } from './experiments.svelte';
import { RESIDENT } from './fixture';

function summary(id: string, kind: ExperimentSummary['kind'], title = id): ExperimentSummary {
	return {
		id,
		kind,
		title,
		model: RESIDENT,
		created_at: '2026-11-01T12:00:00Z',
		expires_at: '2027-01-30T12:00:00Z',
	};
}

const RUN: NeuronsRun = {
	kind: 'neurons',
	request: { prompt: 'hello', chat: false, interventions: [], max_new_tokens: 4 },
};

/**
 * The page's own experiment routes, over a list held here, keeping every
 * request. `answer` overrides what one route says.
 */
function primer(saved: ExperimentSummary[], answer: Record<string, () => Response> = {}) {
	const asked: string[] = [];
	const bodies: unknown[] = [];
	const fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
		const method = init.method ?? 'GET';
		const path = String(input);
		const key = `${method} ${path}`;
		asked.push(key);
		if (init.body) bodies.push(JSON.parse(String(init.body)));
		if (answer[key]) return answer[key]();
		if (key === 'GET /lab/experiments') return Response.json(saved);
		if (key === 'POST /lab/experiments') {
			saved = [summary('new', 'neurons', 'Ablation'), ...saved];
			return Response.json(saved[0], { status: 201 });
		}
		const id = decodeURIComponent(path.split('/')[3]);
		if (method === 'DELETE') {
			saved = saved.filter((each) => each.id !== id);
			return new Response(null, { status: 204 });
		}
		if (method === 'PATCH') {
			const { title } = JSON.parse(String(init.body));
			saved = saved.map((each) => (each.id === id ? { ...each, title } : each));
			return Response.json(saved.find((each) => each.id === id));
		}
		throw new Error(`nothing answers ${key}`);
	}) as typeof globalThis.fetch;
	return { fetch, asked, bodies };
}

describe('saved experiments', () => {
	it('lists only the ones this tab can open', async () => {
		const { fetch } = primer([summary('n', 'neurons'), summary('a', 'anomalies')]);
		const experiments = new Experiments('neurons', { fetch });

		await experiments.load();

		expect(experiments.items.map((each) => each.id)).toEqual(['n']);
	});

	it('saves a run under its title and lists them again, rather than appending', async () => {
		const { fetch, asked, bodies } = primer([summary('old', 'neurons')]);
		const experiments = new Experiments('neurons', { fetch });

		expect(await experiments.save('Ablation', RUN)).toBe(true);

		expect(asked).toEqual(['POST /lab/experiments', 'GET /lab/experiments']);
		expect(bodies).toEqual([{ title: 'Ablation', run: RUN }]);
		expect(experiments.items.map((each) => each.title)).toEqual(['Ablation', 'old']);
		expect(experiments.busy).toBe(false);
	});

	it('renames and deletes the one asked for, and reads the list from Lab after each', async () => {
		const { fetch, asked } = primer([summary('a', 'neurons'), summary('b', 'neurons')]);
		const experiments = new Experiments('neurons', { fetch });

		expect(await experiments.rename('a', 'Renamed')).toBe(true);
		expect(await experiments.remove('b')).toBe(true);

		expect(asked).toEqual([
			'PATCH /lab/experiments/a',
			'GET /lab/experiments',
			'DELETE /lab/experiments/b',
			'GET /lab/experiments',
		]);
		expect(experiments.items.map((each) => each.title)).toEqual(['Renamed']);
	});

	it('opens one by restoring it, so the vectors and probes it names are held again', async () => {
		const restored = { ...summary('a', 'neurons'), run: RUN, vectors: [], probes: [] };
		const { fetch, asked } = primer([], {
			'POST /lab/experiments/a/restore': () => Response.json(restored),
		});
		const experiments = new Experiments('neurons', { fetch });

		expect(await experiments.open('a')).toEqual(restored);
		expect(asked).toEqual(['POST /lab/experiments/a/restore']);
	});

	it("says why one cannot be opened, in Lab's words, and opens nothing", async () => {
		const { fetch } = primer([], {
			'POST /lab/experiments/a/restore': () =>
				Response.json(
					{ title: 'Saved on another model', status: 409, detail: 'Switch Lab to it.' },
					{ status: 409 },
				),
		});
		const experiments = new Experiments('neurons', { fetch });

		expect(await experiments.open('a')).toBeNull();
		expect(experiments.failure).toEqual({
			title: 'Saved on another model',
			detail: 'Switch Lab to it.',
		});
	});

	it('sends a second save while the first is still going nowhere', async () => {
		const { fetch, asked } = primer([]);
		const experiments = new Experiments('neurons', { fetch });

		const first = experiments.save('Ablation', RUN);
		expect(await experiments.save('Ablation', RUN)).toBe(false);
		await first;

		expect(asked.filter((each) => each.startsWith('POST'))).toHaveLength(1);
	});
});
