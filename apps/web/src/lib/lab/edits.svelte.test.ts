import { describe, expect, it } from 'vitest';
import { Edits, Generator, Steering } from './edits.svelte';
import { RESIDENT } from './fixture';

const neuron = { site: 'mlp_neuron' as const, layer: 1, index: 4, op: 'zero' as const };

describe('the list of edits', () => {
	it('adds an edit with its operation’s starting value and every position', () => {
		const edits = new Edits();

		edits.add({ ...neuron, op: 'scale' });

		expect(edits.interventions).toEqual([{ ...neuron, op: 'scale', value: 2, positions: 'all' }]);
	});

	it('changes an edit by replacing it, so what was already read is not written into', () => {
		const edits = new Edits();
		const key = edits.add({ ...neuron, op: 'clamp' });
		const before = edits.items[0];

		edits.update(key, { value: 3 });

		expect(edits.items[0].value).toBe(3);
		expect(edits.items[0]).not.toBe(before);
		expect(before.value).toBe(10);
	});

	it('gives a new operation its own starting value, unless one is given', () => {
		const edits = new Edits();
		const key = edits.add({ ...neuron, op: 'clamp' });

		edits.update(key, { op: 'scale' });
		expect(edits.items[0].value).toBe(2);

		edits.update(key, { op: 'add', value: -1 });
		expect(edits.items[0].value).toBe(-1);
	});

	it('takes a new order without writing into the edits it moves', () => {
		const edits = new Edits();
		edits.add({ ...neuron, index: 1 });
		edits.add({ ...neuron, index: 2 });
		const [first, second] = edits.items;

		edits.reorder([second, first]);

		expect(edits.interventions.map((each) => each.index)).toEqual([2, 1]);
		expect(edits.items[0]).not.toBe(second);
		expect([first.index, second.index]).toEqual([1, 2]);
	});

	it('ignores an order that is not the same edits, so none can be lost or doubled', () => {
		const edits = new Edits();
		edits.add({ ...neuron, index: 1 });
		edits.add({ ...neuron, index: 2 });
		const [first] = edits.items;

		edits.reorder([first, first]);

		expect(edits.interventions.map((each) => each.index)).toEqual([1, 2]);
	});

	it('removes only the edit asked for', () => {
		const edits = new Edits();
		edits.add({ ...neuron, index: 1 });
		const key = edits.add({ ...neuron, index: 2 });
		const kept = edits.items[0];

		edits.remove(key);

		expect(edits.interventions.map((each) => each.index)).toEqual([1]);
		expect(edits.items[0]).toBe(kept);
	});

	it('opens the edit just added, and the next one along when the open one goes', () => {
		const edits = new Edits();
		const first = edits.add({ ...neuron, index: 1 });
		const second = edits.add({ ...neuron, index: 2 });
		const third = edits.add({ ...neuron, index: 3 });
		expect(edits.current?.index).toBe(3);

		edits.active = second;
		edits.remove(second);
		expect(edits.active).toBe(third);

		edits.remove(third);
		expect(edits.active).toBe(first);

		edits.remove(first);
		expect(edits.current).toBeUndefined();
	});

	it('sends Lab the edits without the keys the list tracks them by', () => {
		const edits = new Edits();
		edits.add(neuron);

		expect(Object.keys(edits.interventions[0])).not.toContain('key');
	});

	it('starts again from a saved run’s edits, with none open', () => {
		const edits = new Edits();
		edits.add(neuron);

		edits.replace([
			{ ...neuron, value: 0, positions: 'all' },
			{ ...neuron, op: 'scale', value: 2, positions: 'prompt' },
		]);

		expect(edits.interventions.map((edit) => edit.op)).toEqual(['zero', 'scale']);
		expect(new Set(edits.items.map((edit) => edit.key)).size).toBe(2);
		expect(edits.active).toBeNull();
	});
});

function frames(...events: Record<string, unknown>[]): string {
	return events
		.map((event) => `id: ${event.id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`)
		.join('');
}

function recording(answer: () => Response) {
	const asked: unknown[] = [];
	const fetch = (async (_input: RequestInfo | URL, init: RequestInit = {}) => {
		asked.push(init.body ? JSON.parse(String(init.body)) : null);
		return answer();
	}) as typeof globalThis.fetch;
	return { fetch, asked };
}

describe('generating', () => {
	const ask = { prompt: ' hi ', chat: false, interventions: [], maxNewTokens: 4, target: '' };

	it('reads the stream into the view and asks with the prompt trimmed and no blank target', async () => {
		const token = {
			id: 0,
			type: 'token',
			run: 'baseline',
			index: 0,
			token: { id: 5, text: 'x', special: false },
		};
		const { fetch, asked } = recording(
			() => new Response(frames(token), { headers: { 'Content-Type': 'text/event-stream' } }),
		);
		const generator = new Generator({ fetch });

		await generator.run(ask);

		expect(asked).toEqual([
			{ prompt: 'hi', chat: false, interventions: [], max_new_tokens: 4, target: null },
		]);
		expect(generator.view.baseline.map((each) => each.id)).toEqual([5]);
		expect(generator.running).toBe(false);
	});

	it('says a stream that ended before Lab finished did not finish', async () => {
		const { fetch } = recording(() => new Response(''));
		const generator = new Generator({ fetch });

		await generator.run(ask);

		expect(generator.view.failure?.title).toBe('The generation stopped');
	});

	it('shows a refusal in Lab’s words', async () => {
		const { fetch } = recording(() =>
			Response.json(
				{
					code: 'validation_failed',
					title: 'Edit refused',
					status: 422,
					detail: 'Edit 1: there is no layer 9.',
				},
				{ status: 422 },
			),
		);
		const generator = new Generator({ fetch });

		await generator.run(ask);

		expect(generator.view.failure).toEqual({
			title: 'Edit refused',
			detail: 'Edit 1: there is no layer 9.',
		});
	});

	it('keeps what it was asked, which is what a save stores beside the answers', async () => {
		const { fetch } = recording(
			() => new Response('', { headers: { 'Content-Type': 'text/event-stream' } }),
		);
		const generator = new Generator({ fetch });

		await generator.run({ ...ask, target: ' Paris' });

		expect(generator.asked).toEqual({
			prompt: 'hi',
			chat: false,
			interventions: [],
			max_new_tokens: 4,
			target: ' Paris',
		});
	});

	it('shows a saved run as finished, without asking Lab for anything', () => {
		const { fetch, asked } = recording(() => {
			throw new Error('a saved run is not generated again');
		});
		const generator = new Generator({ fetch });
		const x = { id: 5, text: 'x', special: false };
		const y = { id: 6, text: 'y', special: false };
		const request = { prompt: 'hi', chat: false, interventions: [], max_new_tokens: 1 };

		generator.show(request, {
			type: 'completed',
			id: 3,
			model: RESIDENT,
			prompt: [],
			baseline: [x],
			edited: [y],
			diverged_at: 0,
		});

		expect(asked).toEqual([]);
		expect(generator.asked).toEqual(request);
		expect(generator.view.baseline).toEqual([x]);
		expect(generator.view.edited).toEqual([y]);
		expect(generator.view.completed?.diverged_at).toBe(0);
		expect(generator.running).toBe(false);
	});
});

describe('steering vectors', () => {
	it('lists the vectors again after making one, rather than appending', async () => {
		const made = {
			id: 'a'.repeat(32),
			label: 'formal',
			layer: 1,
			norm: 2,
			positive: 1,
			negative: 1,
		};
		const paths: string[] = [];
		const fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
			paths.push(`${init.method ?? 'GET'} ${String(input)}`);
			return init.method === 'POST' ? Response.json(made, { status: 201 }) : Response.json([made]);
		}) as typeof globalThis.fetch;
		const steering = new Steering({ fetch });

		const vector = await steering.create({
			label: 'formal',
			layer: 1,
			positive: ['a'],
			negative: ['b'],
			chat: true,
		});

		expect(vector?.id).toBe(made.id);
		expect(paths).toEqual(['POST /lab/vectors', 'GET /lab/vectors']);
		expect(steering.label(made.id)).toBe('formal');
	});
});
