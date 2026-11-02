import { describe, expect, it } from 'vitest';
import { comfyProvider, image, openaiProvider, provenance } from './fixture';
import { Generator } from './generator.svelte';
import { offersFor } from './offer';

const OFFERS = offersFor([comfyProvider(), openaiProvider()], 'generate');

/** An SSE body, one event a frame, the way Control writes it. */
function stream(...events: object[]): Response {
	const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');
	return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
}

/** The page's own endpoints, answering from a list and keeping every request. */
function primer(...answers: (() => Response)[]) {
	const asked: { path: string; method: string; body: unknown }[] = [];
	const fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
		asked.push({
			path: String(input),
			method: init.method ?? 'GET',
			body: init.body ? JSON.parse(String(init.body)) : null,
		});
		const answer = answers.shift();
		if (!answer) throw new Error(`nothing answers ${String(input)}`);
		return answer();
	}) as typeof globalThis.fetch;
	return { fetch, asked };
}

describe('a generation', () => {
	it('runs, then adds the images to the top of the history', async () => {
		const made = image('new');
		const { fetch, asked } = primer(() =>
			stream(
				{ type: 'progress', value: 10, max: 30 },
				{ type: 'progress', value: 30, max: 30 },
				{ type: 'done', images: [made] },
			),
		);
		const generator = new Generator(OFFERS, [image('old')], { fetch });
		generator.prompt = 'a lighthouse at dusk';
		generator.seed = 7;

		const running = generator.run();
		expect(generator.running).toBe(true);
		await running;

		expect(asked[0]).toMatchObject({
			path: '/studio/generations',
			method: 'POST',
			body: { provider_id: 'p-comfy', model: 'sdxl', operation: 'generate', count: 1, seed: 7 },
		});
		expect(generator.results).toEqual([made]);
		expect(generator.history.map((each) => each.id)).toEqual(['new', 'old']);
		expect([generator.running, generator.progress, generator.failure]).toEqual([false, null, null]);
	});

	it('keeps the progress the stream last reported while it runs', async () => {
		let release = () => {};
		const body = new ReadableStream<Uint8Array>({
			start(controller) {
				const encode = (event: object) =>
					new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`);
				controller.enqueue(encode({ type: 'progress', value: 4, max: 20 }));
				release = () => {
					controller.enqueue(encode({ type: 'done', images: [image('x')] }));
					controller.close();
				};
			},
		});
		const { fetch } = primer(() => new Response(body));
		const generator = new Generator(OFFERS, [], { fetch });
		generator.prompt = 'a fox';

		const running = generator.run();
		await new Promise((resolve) => setTimeout(resolve, 10));
		expect(generator.progress).toEqual({ value: 4, max: 20 });
		release();
		await running;

		expect(generator.progress).toBeNull();
	});

	it('shows a failure in the provider’s words and leaves the history as it was', async () => {
		const { fetch } = primer(() =>
			stream({
				type: 'failed',
				code: 'dependency_unavailable',
				title: 'Image provider is busy',
				detail: 'ComfyUI is rate limiting requests.',
				status: 503,
			}),
		);
		const generator = new Generator(OFFERS, [image('old')], { fetch });
		generator.prompt = 'a fox';

		await generator.run();

		expect(generator.failure).toEqual({
			title: 'Image provider is busy',
			detail: 'ComfyUI is rate limiting requests.',
		});
		expect(generator.history.map((each) => each.id)).toEqual(['old']);
	});

	it('shows a refusal before the stream opens as the problem Control sent', async () => {
		const { fetch } = primer(() =>
			Response.json(
				{
					code: 'rate_limited',
					title: 'Too many generations',
					status: 429,
					detail: 'Wait an hour.',
				},
				{ status: 429 },
			),
		);
		const generator = new Generator(OFFERS, [], { fetch });
		generator.prompt = 'a fox';

		await generator.run();

		expect(generator.failure).toEqual({ title: 'Too many generations', detail: 'Wait an hour.' });
	});

	it('says so when the stream ends without a result', async () => {
		/** Otherwise the spinner stops and nothing explains the empty grid. */
		const { fetch } = primer(() => stream({ type: 'progress', value: 1, max: 2 }));
		const generator = new Generator(OFFERS, [], { fetch });
		generator.prompt = 'a fox';

		await generator.run();

		expect(generator.failure?.title).toBe('The generation stopped');
	});
});

describe('the form', () => {
	it('sends no setting the picked endpoint did not declare', async () => {
		const { fetch, asked } = primer(() => stream({ type: 'done', images: [image('x')] }));
		const generator = new Generator(OFFERS, [], { fetch });
		generator.prompt = 'a fox';
		generator.seed = 7;
		generator.steps = 30;
		generator.negativePrompt = 'blur';
		generator.pick('p-openai:gpt-image-1');
		generator.count = 3;

		await generator.run();

		expect(asked[0].body).toMatchObject({
			model: 'gpt-image-1',
			count: 3,
			seed: null,
			steps: null,
			negative_prompt: null,
		});
	});

	it('holds a template to one image and a size it declared', () => {
		const generator = new Generator(OFFERS, []);
		generator.pick('p-openai:gpt-image-1');
		generator.count = 4;
		generator.size = '1536x1024';

		generator.pick('p-comfy:sdxl');

		expect([generator.count, generator.size]).toEqual([1, '1024x1024']);
	});

	it('reuses an image’s settings, seed included, to make it again', () => {
		const generator = new Generator(OFFERS, []);
		generator.pick('p-openai:gpt-image-1');

		const reused = generator.reuse(image('a'));

		expect(reused).toBe(true);
		expect(generator.request).toMatchObject({
			provider_id: 'p-comfy',
			model: 'sdxl',
			prompt: 'a lighthouse at dusk',
			size: '1024x1024',
			seed: 7,
			steps: 30,
		});
	});

	it('leaves the form alone for an image whose model is no longer offered', () => {
		const generator = new Generator(OFFERS, []);
		generator.prompt = 'mine';
		const gone = image('a', {
			provenance: provenance({ provider_id: 'removed' }),
		});

		expect(generator.reuse(gone)).toBe(false);
		expect(generator.prompt).toBe('mine');
	});
});

describe('the history', () => {
	it('pages back from the oldest image shown and stops at an empty page', async () => {
		const { fetch, asked } = primer(
			() => Response.json([image('older', { created_at: '2026-10-01T00:00:00Z' })]),
			() => Response.json([]),
		);
		const generator = new Generator(OFFERS, [image('newest')], { fetch });

		await generator.older();
		await generator.older();
		await generator.older();

		expect(asked.map((each) => each.path)).toEqual([
			'/studio/images?before=2026-11-01T12%3A00%3A00Z',
			'/studio/images?before=2026-10-01T00%3A00%3A00Z',
		]);
		expect(generator.history.map((each) => each.id)).toEqual(['newest', 'older']);
		expect(generator.more).toBe(false);
	});

	it('drops a deleted image from the history and the results', async () => {
		const { fetch, asked } = primer(() => new Response(null, { status: 204 }));
		const doomed = image('doomed');
		const generator = new Generator(OFFERS, [doomed, image('kept')], { fetch });
		generator.results = [doomed];

		await generator.remove(doomed);

		expect(asked[0]).toMatchObject({ path: '/studio/images/doomed', method: 'DELETE' });
		expect(generator.history.map((each) => each.id)).toEqual(['kept']);
		expect(generator.results).toEqual([]);
	});
});
