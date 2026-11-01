import { describe, expect, it } from 'vitest';
import { sourcesFrom } from './anomalies';
import { chatModel, RESIDENT, sequence } from './fixture';
import { Scorer } from './scorer.svelte';

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
