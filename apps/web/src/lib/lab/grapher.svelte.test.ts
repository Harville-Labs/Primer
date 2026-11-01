import { describe, expect, it } from 'vitest';
import { builtGraph, RESIDENT } from './fixture';
import { Grapher } from './grapher.svelte';

/** The page's graph endpoint, answering once and keeping what it was sent. */
function primer(answer: () => Response) {
	const asked: { path: string; body: Record<string, unknown> }[] = [];
	const fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
		asked.push({ path: String(input), body: JSON.parse(String(init.body)) });
		return answer();
	}) as typeof globalThis.fetch;
	return { fetch, asked };
}

const GRAPH = {
	model: RESIDENT,
	transcoders: { repository: 'lab/tiny', commit: 'c', state: 'ready', width: 64 },
	graph: builtGraph(),
};

describe('building a graph', () => {
	it('sends the prompt and target exactly as typed, and opens on the likeliest prediction', async () => {
		const { fetch, asked } = primer(() => Response.json(GRAPH));
		const grapher = new Grapher({ fetch });
		grapher.prompt = 'The capital of France is';
		grapher.target = ' Paris';
		grapher.nodeThreshold = 0.6;

		await grapher.build();

		expect(asked).toEqual([
			{
				path: '/lab/graph',
				body: {
					prompt: 'The capital of France is',
					target: ' Paris',
					node_threshold: 0.6,
					edge_threshold: 0.98,
				},
			},
		]);
		expect(grapher.selected).toBe('l1');
		expect(grapher.influence).toBe(0.6);
		expect(grapher.failure).toBeNull();
	});

	it('asks for the likeliest tokens when no target is named, rather than for an empty one', async () => {
		/** Lab refuses an empty target: it is not a token. */
		const { fetch, asked } = primer(() => Response.json(GRAPH));
		const grapher = new Grapher({ fetch });
		grapher.prompt = 'x';

		await grapher.build();

		expect(asked[0]?.body.target).toBeNull();
	});

	it('says why Lab refused, in Lab’s words, and keeps the graph it had', async () => {
		const answers = [
			() => Response.json(GRAPH),
			() =>
				Response.json(
					{ title: 'The transcoders are still downloading', status: 409, detail: 'Try later.' },
					{ status: 409 },
				),
		];
		const { fetch } = primer(() => (answers.shift() ?? answers[0])());
		const grapher = new Grapher({ fetch });
		grapher.prompt = 'x';
		await grapher.build();
		const before = grapher.built;
		expect(before).not.toBeNull();

		await grapher.build();

		expect(grapher.failure).toEqual({
			title: 'The transcoders are still downloading',
			detail: 'Try later.',
		});
		expect(grapher.running).toBe(false);
		expect(grapher.built).toBe(before);
	});

	it('sends nothing without a prompt', async () => {
		const { fetch, asked } = primer(() => Response.json(GRAPH));
		await new Grapher({ fetch }).build();
		expect(asked).toEqual([]);
	});
});
