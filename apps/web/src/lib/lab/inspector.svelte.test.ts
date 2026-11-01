import { describe, expect, it } from 'vitest';
import { reading } from './fixture';
import { Inspector } from './inspector.svelte';

/**
 * A Lab behind the page's own endpoints, answering from a table and keeping
 * every request, so a test can show what was - and was not - asked.
 */
function lab(answers: Record<string, () => Response | Promise<Response>>) {
	const asked: { path: string; body: unknown }[] = [];
	const fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
		const path = String(input);
		asked.push({ path, body: init.body ? JSON.parse(String(init.body)) : null });
		const answer = answers[path];
		if (!answer) throw new Error(`nothing answers ${path}`);
		return answer();
	};
	return { fetch: fetch as typeof globalThis.fetch, asked };
}

function inspector(answers: Record<string, () => Response | Promise<Response>>) {
	const fake = lab(answers);
	return { inspector: new Inspector({ fetch: fake.fetch, pollMs: 5 }), asked: fake.asked };
}

describe('running a prompt', () => {
	it('selects the next-token prediction once the reading arrives', async () => {
		const { inspector: view, asked } = inspector({
			'/lab/inspect': () => Response.json(reading()),
		});
		view.prompt = '  hello world ';
		view.chat = false;

		await view.run(4, 3);

		expect(asked).toEqual([
			{
				path: '/lab/inspect',
				body: { prompt: 'hello world', chat: false, neurons: 4, features: 3 },
			},
		]);
		expect(view.reading?.tokens).toHaveLength(2);
		expect(view.selected).toEqual({ layer: 2, token: 1 });
		expect(view.running).toBe(false);
		expect(view.failure).toBeNull();
	});

	it('says what Lab refused with, and keeps the reading it already had', async () => {
		let refuse = false;
		const { inspector: view } = inspector({
			'/lab/inspect': () =>
				refuse
					? Response.json(
							{
								code: 'validation_failed',
								title: 'The prompt is too long',
								status: 422,
								detail: 'It is 900 tokens, and this Lab reads at most 512.',
								request_id: null,
							},
							{ status: 422 },
						)
					: Response.json(reading()),
		});
		view.prompt = 'hello';
		await view.run();
		refuse = true;

		await view.run();

		expect(view.failure).toEqual({
			title: 'The prompt is too long',
			detail: 'It is 900 tokens, and this Lab reads at most 512.',
		});
		expect(view.reading).not.toBeNull();
		expect(view.running).toBe(false);
	});

	it('tells Primer not answering apart from Lab refusing', async () => {
		const { inspector: view } = inspector({
			'/lab/inspect': () => Promise.reject(new TypeError('Failed to fetch')),
		});
		view.prompt = 'hello';

		await view.run();

		expect(view.failure?.title).toBe('Primer did not answer');
	});

	it('shows how many runs are ahead while it waits, and stops asking once it is answered', async () => {
		let answer: (response: Response) => void = () => {};
		const { inspector: view, asked } = inspector({
			'/lab/inspect': () => new Promise((resolve) => (answer = resolve)),
			'/lab/status': () => Response.json({ queue_depth: 3 }),
		});
		view.prompt = 'hello';

		const running = view.run();
		await waitFor(() => view.ahead !== null);
		expect(view.ahead).toBe(2);
		answer(Response.json(reading()));
		await running;

		expect(view.ahead).toBeNull();
		const polls = asked.filter((call) => call.path === '/lab/status').length;
		await new Promise((resolve) => setTimeout(resolve, 30));
		expect(asked.filter((call) => call.path === '/lab/status')).toHaveLength(polls);
	});

	it('asks for nothing with an empty prompt', async () => {
		const { inspector: view, asked } = inspector({});
		view.prompt = '   ';
		await view.run();
		expect(asked).toEqual([]);
	});
});

describe('moving around a reading', () => {
	async function ready() {
		const made = inspector({ '/lab/inspect': () => Response.json(reading()) });
		made.inspector.prompt = 'hello';
		await made.inspector.run();
		return made;
	}

	it('moves the selection with the keyboard and stays inside the grid', async () => {
		const { inspector: view } = await ready();

		expect(view.key('ArrowDown')).toBe(true);
		expect(view.selected).toEqual({ layer: 1, token: 1 });
		expect(view.key('ArrowRight')).toBe(true);
		expect(view.selected).toEqual({ layer: 1, token: 1 });
		expect(view.key('Home')).toBe(true);
		expect(view.selected).toEqual({ layer: 1, token: 0 });
		expect(view.key('a')).toBe(false);
	});

	it('ignores a click outside the reading', async () => {
		const { inspector: view } = await ready();
		view.select({ layer: 9, token: 0 });
		expect(view.selected).toEqual({ layer: 2, token: 1 });
	});

	it('handles no keys before there is anything to move around', () => {
		expect(new Inspector({ fetch: lab({}).fetch }).key('ArrowUp')).toBe(false);
	});
});

describe('reading a whole vector', () => {
	const vector = (model = reading().model) =>
		Response.json({ model, layer: 2, token: 1, site: 'mlp_neuron', values: [1, -2] });

	it('reads it for the prompt the grid came from, not whatever the composer says now', async () => {
		const { inspector: view, asked } = inspector({
			'/lab/inspect': () => Response.json(reading()),
			'/lab/activation': () => vector(),
		});
		view.prompt = 'hello';
		await view.run();
		view.prompt = 'something else entirely';
		view.site = 'mlp_neuron';

		await view.readVector();

		expect(asked.at(-1)).toEqual({
			path: '/lab/activation',
			body: { prompt: 'hello', chat: true, layer: 2, token: 1, site: 'mlp_neuron' },
		});
		expect(view.vector?.values).toEqual([1, -2]);
		expect(view.vector?.stale).toBe(false);
	});

	it('marks the vector stale once the selection moves off its cell', async () => {
		const { inspector: view } = inspector({
			'/lab/inspect': () => Response.json(reading()),
			'/lab/activation': () => vector(),
		});
		view.prompt = 'hello';
		await view.run();
		await view.readVector();

		view.key('ArrowDown');

		expect(view.vector?.stale).toBe(true);
	});

	it('refuses a vector from a model other than the one the grid was read on', async () => {
		const other = { ...reading().model, reference: { source: 'local' as const, path: 'other' } };
		const { inspector: view } = inspector({
			'/lab/inspect': () => Response.json(reading()),
			'/lab/activation': () => vector(other),
		});
		view.prompt = 'hello';
		await view.run();

		await view.readVector();

		expect(view.vector).toBeNull();
		expect(view.vectorFailure?.title).toBe('The model changed');
	});
});

async function waitFor(condition: () => boolean): Promise<void> {
	for (let i = 0; i < 100 && !condition(); i++) {
		await new Promise((resolve) => setTimeout(resolve, 5));
	}
	expect(condition()).toBe(true);
}
