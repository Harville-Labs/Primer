import { describe, expect, it } from 'vitest';
import { sourcesFrom } from './anomalies';
import { chatModel, PROBE, RESIDENT, sequence } from './fixture';
import { Probes, Scorer } from './scorer.svelte';

/** The page's own endpoints, answering from a table and keeping every request. */
function primer(answers: Record<string, () => Response>) {
	const asked: { path: string; body: Record<string, unknown> }[] = [];
	const fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
		const path = String(input);
		asked.push({ path, body: JSON.parse(String(init.body)) });
		const answer = answers[path];
		if (!answer) throw new Error(`nothing answers ${path}`);
		return answer();
	}) as typeof globalThis.fetch;
	return { fetch, asked };
}

const SOURCES = sourcesFrom(
	[chatModel('answers'), chatModel('reads', { provider_id: 'p2', supports_prompt_logprobs: true })],
	RESIDENT,
);

describe('scoring', () => {
	it('asks Chat for the picked endpoint, its top 20, and opens on the most surprising token', async () => {
		const { fetch, asked } = primer({
			'/chat/score': () => Response.json({ text: 'answer', score: sequence([1, 6, 2]) }),
		});
		const scorer = new Scorer(SOURCES, { fetch });
		scorer.text = ' Why?';

		await scorer.run();

		expect(asked).toEqual([
			{
				path: '/chat/score',
				body: {
					provider_id: 'p1',
					model: 'answers',
					mode: 'generate',
					text: ' Why?',
					top_k: 20,
					max_tokens: 128,
				},
			},
		]);
		expect(scorer.scored?.text).toBe('answer');
		expect(scorer.selected).toBe(1);
		expect(scorer.failure).toBeNull();
	});

	it('generates on an endpoint without prompt logprobs, whatever was picked before it', async () => {
		/**
		 * The switch is hidden for such an endpoint; a "text" chosen on the
		 * previous source must not still be sent, or Chat refuses every run.
		 */
		const { fetch, asked } = primer({
			'/chat/score': () => Response.json({ text: 'x', score: sequence([1]) }),
		});
		const scorer = new Scorer(SOURCES, { fetch });
		scorer.sourceKey = SOURCES[1].key;
		scorer.mode = 'text';
		scorer.sourceKey = SOURCES[0].key;
		scorer.text = 'some text';

		await scorer.run();

		expect(asked[0].body.mode).toBe('generate');
	});

	it('scores text on Lab, held to the answer length Lab allows', async () => {
		const { fetch, asked } = primer({
			'/lab/score': () =>
				Response.json({ text: 'hello', score: sequence([1], { estimate: 'full', top_k: null }) }),
		});
		const scorer = new Scorer(SOURCES, { fetch, labMaxNewTokens: 32 });
		scorer.sourceKey = 'lab';
		scorer.mode = 'text';
		scorer.text = 'hello world';

		await scorer.run();

		expect(asked).toEqual([
			{
				path: '/lab/score',
				body: { prompt: 'hello world', mode: 'text', chat: true, max_new_tokens: 32 },
			},
		]);
		expect(scorer.scored?.score.estimate).toBe('full');
	});

	it("says what was refused in the service's words, and keeps the last score", async () => {
		let refuse = false;
		const { fetch } = primer({
			'/chat/score': () =>
				refuse
					? Response.json(
							{ code: 'x', title: 'Logprobs not declared', status: 422, detail: 'Declare it.' },
							{ status: 422 },
						)
					: Response.json({ text: 'first', score: sequence([1]) }),
		});
		const scorer = new Scorer(SOURCES, { fetch });
		scorer.text = 'a';
		await scorer.run();
		refuse = true;

		await scorer.run();

		expect(scorer.failure).toEqual({ title: 'Logprobs not declared', detail: 'Declare it.' });
		expect(scorer.scored?.text).toBe('first');
		expect(scorer.running).toBe(false);
	});

	it('sends nothing for blank text', async () => {
		const { fetch, asked } = primer({});
		const scorer = new Scorer(SOURCES, { fetch });
		scorer.text = '   ';

		await scorer.run();

		expect(asked).toEqual([]);
	});
});

describe("Lab's detectors", () => {
	const OUTLIERS = { layer: 1, distances: [3], threshold: 2, reference_tokens: 100 };

	it('asks Lab for the picked layer and probe, and keeps what it read', async () => {
		const { fetch, asked } = primer({
			'/lab/score': () =>
				Response.json({
					text: 'hi',
					score: sequence([1]),
					outliers: OUTLIERS,
					probe: { probe: PROBE, probabilities: [0.9] },
				}),
		});
		const scorer = new Scorer(SOURCES, { fetch });
		scorer.sourceKey = 'lab';
		scorer.text = 'hi';
		scorer.outlierLayer = 1;
		scorer.probeId = PROBE.id;

		await scorer.run();

		expect(asked[0].body).toMatchObject({ outlier_layer: 1, probe_id: PROBE.id });
		expect(scorer.scored?.outliers).toEqual(OUTLIERS);
		expect(scorer.scored?.probe?.probabilities).toEqual([0.9]);
		expect(scorer.scored?.lab?.request).toEqual(asked[0].body);
	});

	it('puts a saved Lab score back with the choices that made it', () => {
		const { fetch, asked } = primer({});
		const scorer = new Scorer(SOURCES, { fetch });
		const request = {
			prompt: 'the text',
			mode: 'text' as const,
			chat: true,
			max_new_tokens: 16,
			outlier_layer: 1,
			probe_id: PROBE.id,
		};
		const result = {
			mode: 'text' as const,
			model: RESIDENT,
			text: 'the text',
			score: sequence([1, 7]),
			outliers: OUTLIERS,
			probe: null,
		};

		expect(scorer.show(request, result)).toBe(true);

		expect(asked).toEqual([]);
		expect(scorer.source?.kind).toBe('lab');
		expect([scorer.mode, scorer.text, scorer.outlierLayer, scorer.probeId]).toEqual([
			'text',
			'the text',
			1,
			PROBE.id,
		]);
		expect(scorer.scored?.outliers).toEqual(OUTLIERS);
		expect(scorer.scored?.lab).toEqual({ request, result });
		expect(scorer.selected).toBe(1);
	});

	it('cannot put a Lab score back on a page offered no Lab', () => {
		const scorer = new Scorer(sourcesFrom([chatModel('answers')], null));

		expect(
			scorer.show(
				{ prompt: 'x', mode: 'text', chat: true, max_new_tokens: 1 },
				{ mode: 'text', model: RESIDENT, text: 'x', score: sequence([1]) },
			),
		).toBe(false);
		expect(scorer.scored).toBeNull();
	});

	it('are never sent to an endpoint, which has no residual stream to read', async () => {
		/**
		 * Chat would refuse a field it does not know, so a detector picked
		 * while Lab was the source must not follow the reader to an endpoint.
		 */
		const { fetch, asked } = primer({
			'/chat/score': () => Response.json({ text: 'x', score: sequence([1]) }),
		});
		const scorer = new Scorer(SOURCES, { fetch });
		scorer.outlierLayer = 1;
		scorer.probeId = PROBE.id;
		scorer.text = 'a';

		await scorer.run();

		expect(Object.keys(asked[0].body)).not.toContain('outlier_layer');
		expect(Object.keys(asked[0].body)).not.toContain('probe_id');
		expect(scorer.scored?.outliers).toBeNull();
		expect(scorer.scored?.probe).toBeNull();
		expect(scorer.scored?.lab, "an endpoint's score is not Lab's to save").toBeNull();
	});
});

describe('probes', () => {
	it('lists the probes again after training one, rather than appending', async () => {
		const paths: string[] = [];
		const fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
			paths.push(`${init.method ?? 'GET'} ${String(input)}`);
			return init.method === 'POST'
				? Response.json(PROBE, { status: 201 })
				: Response.json([PROBE]);
		}) as typeof globalThis.fetch;
		const probes = new Probes({ fetch });

		const made = await probes.create({
			label: 'formal',
			layer: 1,
			positive: ['a', 'b', 'c', 'd'],
			negative: ['e', 'f', 'g', 'h'],
			chat: false,
		});

		expect(made?.id).toBe(PROBE.id);
		expect(paths).toEqual(['POST /lab/probes', 'GET /lab/probes']);
		expect(probes.find(PROBE.id)?.accuracy).toBe(0.875);
	});

	it("says why training was refused, in Lab's words", async () => {
		const fetch = (async () =>
			Response.json(
				{
					code: 'x',
					title: 'No such layer',
					status: 422,
					detail: "This model's layers are 0 to 1.",
				},
				{ status: 422 },
			)) as unknown as typeof globalThis.fetch;
		const probes = new Probes({ fetch });

		const made = await probes.create({
			label: 'l',
			layer: 9,
			positive: ['a'],
			negative: ['b'],
			chat: false,
		});

		expect(made).toBeNull();
		expect(probes.failure).toEqual({
			title: 'No such layer',
			detail: "This model's layers are 0 to 1.",
		});
		expect(probes.creating).toBe(false);
	});
});

describe('moving along the strip', () => {
	it('stops at both ends', async () => {
		const { fetch } = primer({
			'/chat/score': () => Response.json({ text: 'x', score: sequence([9, 1, 1]) }),
		});
		const scorer = new Scorer(SOURCES, { fetch });
		scorer.text = 'a';
		await scorer.run();

		expect(scorer.key('ArrowLeft')).toBe(true);
		expect(scorer.selected).toBe(0);
		scorer.key('End');
		scorer.key('ArrowRight');
		expect(scorer.selected).toBe(2);
		expect(scorer.key('ArrowUp')).toBe(false);
	});
});
