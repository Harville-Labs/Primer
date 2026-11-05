import { describe, expect, it } from 'vitest';
import type { InspectedToken, SteeredChatRequest } from '#lib/api/types.ts';
import { SteeredChat } from './chat.svelte';
import { frames, RESIDENT } from './fixture';

const shift = { site: 'mlp_neuron' as const, layer: 1, index: 2, op: 'add' as const, value: 4 };

function token(text: string, id = 7): InspectedToken {
	return { id, text, special: false };
}

/** Lab's whole answer to one turn: the unsteered reply, then the steered one. */
function reply(baseline: string, edited: string, editedText: string | null = edited) {
	return frames(
		{ id: 0, type: 'token', run: 'baseline', index: 0, token: token(baseline) },
		{ id: 1, type: 'token', run: 'edited', index: 0, token: token(edited) },
		{
			id: 2,
			type: 'completed',
			model: RESIDENT,
			prompt: [],
			baseline: [token(baseline)],
			edited: [token(edited)],
			diverged_at: baseline === edited ? null : 0,
			edited_text: editedText,
		},
	);
}

/** A fetch that answers each request with the next of `answers`, recording what it was sent. */
function lab(...answers: (() => Response)[]) {
	const asked: { path: string; body: SteeredChatRequest }[] = [];
	const fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
		asked.push({ path: String(input), body: JSON.parse(String(init.body)) });
		const answer = answers.shift();
		if (!answer) throw new Error('Lab was asked for more turns than the test expected');
		return answer();
	}) as typeof globalThis.fetch;
	return { fetch, asked };
}

const stream = (body: string) => () =>
	new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });

describe('a steered chat', () => {
	it('sends the steered reply back as the assistant’s turn, with the edits active now', async () => {
		const { fetch, asked } = lab(stream(reply('plain', 'steered')), stream(reply('a', 'b')));
		const chat = new SteeredChat({ fetch });

		await chat.send(' hello ', [shift], 8);
		await chat.send('and then?', [], 8);

		expect(asked.map((each) => each.path)).toEqual(['/lab/chat', '/lab/chat']);
		expect(asked[0].body).toEqual({
			messages: [{ role: 'user', content: 'hello' }],
			interventions: [shift],
			max_new_tokens: 8,
		});
		expect(asked[1].body).toEqual({
			messages: [
				{ role: 'user', content: 'hello' },
				{ role: 'assistant', content: 'steered' },
				{ role: 'user', content: 'and then?' },
			],
			interventions: [],
			max_new_tokens: 8,
		});
	});

	it('keeps both replies and the steering each was made with', async () => {
		const { fetch } = lab(stream(reply('plain', 'steered')));
		const chat = new SteeredChat({ fetch });

		await chat.send('hello', [shift], 8);

		const [exchange] = chat.exchanges;
		expect(exchange.steering).toEqual([shift]);
		expect(exchange.view.baseline.map((each) => each.text)).toEqual(['plain']);
		expect(exchange.view.edited.map((each) => each.text)).toEqual(['steered']);
		expect(chat.running).toBe(false);
	});

	it('sends the reply as Lab decoded it, not the tokens’ texts joined', async () => {
		const { fetch, asked } = lab(stream(reply('x', 'caf', 'café')), stream(reply('a', 'b')));
		const chat = new SteeredChat({ fetch });

		await chat.send('hello', [], 8);
		await chat.send('more', [], 8);

		expect(asked[1].body.messages[1]).toEqual({ role: 'assistant', content: 'café' });
	});

	it('leaves out a turn that failed, so the next is not sent after an unanswered one', async () => {
		const refused = () =>
			Response.json(
				{ code: 'validation_failed', title: 'That edit cannot be made', status: 422 },
				{ status: 422 },
			);
		const { fetch, asked } = lab(refused, stream(reply('a', 'b')));
		const chat = new SteeredChat({ fetch });

		await chat.send('first', [shift], 8);
		await chat.send('second', [], 8);

		expect(chat.exchanges[0].view.failure?.title).toBe('That edit cannot be made');
		expect(chat.kept(chat.exchanges[0])).toBe(false);
		expect(asked[1].body.messages).toEqual([{ role: 'user', content: 'second' }]);
	});

	it('leaves out a steered reply with nothing in it, which Lab would refuse as a turn', async () => {
		const { fetch, asked } = lab(stream(reply('a', '<eos>', '')), stream(reply('a', 'b')));
		const chat = new SteeredChat({ fetch });

		await chat.send('first', [], 8);
		await chat.send('second', [], 8);

		expect(asked[1].body.messages).toEqual([{ role: 'user', content: 'second' }]);
	});

	it('says a reply cut off before Lab finished did not finish', async () => {
		const { fetch } = lab(stream(''));
		const chat = new SteeredChat({ fetch });

		await chat.send('hello', [], 8);

		expect(chat.exchanges[0].view.failure?.title).toBe('The generation stopped');
	});

	it('starts again empty, and sends nothing for a blank turn', async () => {
		const { fetch, asked } = lab(stream(reply('a', 'b')));
		const chat = new SteeredChat({ fetch });
		await chat.send('hello', [], 8);

		chat.clear();
		await chat.send('   ', [], 8);

		expect(chat.exchanges).toEqual([]);
		expect(chat.messages).toEqual([]);
		expect(asked).toHaveLength(1);
	});
});
