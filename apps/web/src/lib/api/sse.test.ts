import { describe, expect, it } from 'vitest';
import { emptyStream, parseEvents, reduce } from './sse';

/** A byte stream delivered in the chunks given, as a connection would. */
function streamOf(...chunks: Uint8Array[]): ReadableStream<Uint8Array> {
	return new ReadableStream({
		start(controller) {
			for (const chunk of chunks) controller.enqueue(chunk);
			controller.close();
		},
	});
}

async function drain(body: ReadableStream<Uint8Array>) {
	const events = [];
	for await (const event of parseEvents(body)) events.push(event);
	return events;
}

/** Everything parsed out of a stream written as text. */
function collect(...chunks: string[]) {
	const encoder = new TextEncoder();
	return drain(streamOf(...chunks.map((chunk) => encoder.encode(chunk))));
}

describe('SSE reduction', () => {
	it('coalesces deltas in arrival order', () => {
		let state = emptyStream();
		state = reduce(state, { type: 'message.delta', id: 0, text: 'Grounded ' });
		state = reduce(state, { type: 'message.delta', id: 1, text: 'answer.' });

		// The space between words routinely arrives as a fragment's trailing
		// character; joining without care welds them together.
		expect(state.text).toBe('Grounded answer.');
	});

	it('ignores an event it has already seen', () => {
		// A reconnect replays from a point the client saw; applying those
		// deltas twice duplicates text mid-answer.
		let state = emptyStream();
		state = reduce(state, { type: 'message.delta', id: 0, text: 'once' });
		state = reduce(state, { type: 'message.delta', id: 0, text: 'once' });

		expect(state.text).toBe('once');
	});

	it('keeps the text it accumulated when the stream errors', () => {
		let state = emptyStream();
		state = reduce(state, { type: 'message.delta', id: 0, text: 'Partial ' });
		state = reduce(state, { type: 'error', id: 1, code: 'generation_failed', detail: null });

		expect(state.text).toBe('Partial ');
		expect(state.error?.code).toBe('generation_failed');
		expect(state.done).toBe(true);
	});

	it('collects citations before any text arrives', () => {
		let state = emptyStream();
		state = reduce(state, {
			type: 'citation',
			id: 0,
			citation: {
				document_id: 'd',
				document_version_id: 'v',
				chunk_id: 'c',
				locator: null,
				excerpt: null,
			},
		});

		expect(state.citations).toHaveLength(1);
		expect(state.text).toBe('');
	});

	it('skips an event type it does not know', () => {
		// A newer server must not make an older browser render nonsense.
		let state = emptyStream();
		state = reduce(state, { type: 'tool.someday', id: 0 });

		expect(state.done).toBe(false);
		expect(state.text).toBe('');
		expect(state.lastEventId).toBe(0);
	});

	it('takes the completed message as final', () => {
		let state = emptyStream();
		state = reduce(state, { type: 'message.delta', id: 0, text: 'draft' });
		state = reduce(state, {
			type: 'message.completed',
			id: 1,
			message: {
				id: 'm',
				conversation_id: 'c',
				role: 'assistant',
				state: 'completed',
				content: 'the stored answer',
				citations: [],
				error_code: null,
				created_at: '2026-08-31T00:00:00Z',
			},
		});

		expect(state.text).toBe('the stored answer');
		expect(state.done).toBe(true);
	});

	it('reads a framed event', async () => {
		const events = await collect('id: 3\nevent: message.delta\ndata: {"id":3,"text":"hi"}\n\n');
		expect(events).toEqual([{ id: 3, text: 'hi' }]);
	});

	it('skips a malformed frame rather than aborting', async () => {
		// The rest of the answer is still worth showing, so a bad payload
		// costs one event rather than the whole answer.
		const events = await collect(
			'id: 3\ndata: {"id":3,\n\n: keepalive\n\ndata: {"id":4,"text":"rest"}\n\n',
		);
		expect(events).toEqual([{ id: 4, text: 'rest' }]);
	});
});

/**
 * Framing the hand-written parser got wrong.
 *
 * It matched the one shape Primer's own server sends - `\n\n` separators and
 * `data:` with exactly one space - rather than the specification. Both of
 * these are legal SSE that it dropped or hung on, and both would have
 * arrived the moment anything between the server and the browser rewrote
 * the stream.
 */
describe('SSE framing that is legal but not what we send', () => {
	it('reads data with no space after the colon', async () => {
		// The space is optional in the specification. This was silently
		// dropped: no error, just an event that never arrived.
		expect(await collect('data:{"id":1,"text":"hi"}\n\n')).toEqual([{ id: 1, text: 'hi' }]);
	});

	it('reads frames separated by CRLF', async () => {
		// `indexOf('\n\n')` never finds `\r\n\r\n`, so the old parser
		// buffered such a stream forever and yielded nothing at all.
		expect(await collect('data: {"id":1,"text":"hi"}\r\n\r\n')).toEqual([{ id: 1, text: 'hi' }]);
	});

	it('reassembles an event split across chunks', async () => {
		// The case the old parser did handle, kept because it is the one
		// that happens on every real connection.
		expect(await collect('data: {"id":1,', '"text":"hi"}\n\n')).toEqual([{ id: 1, text: 'hi' }]);
	});

	it('reassembles a multi-byte character split across chunks', async () => {
		const bytes = new TextEncoder().encode('data: {"id":1,"text":"é"}\n\n');
		const events = await drain(streamOf(bytes.slice(0, 22), bytes.slice(22)));
		expect(events).toEqual([{ id: 1, text: 'é' }]);
	});
});

describe('a model that thinks aloud', () => {
	it('keeps thinking out of the answer', () => {
		// The one that matters: the answer is what a reader copies and cites.
		const events = [
			{ id: 0, type: 'reasoning.delta', text: 'weighing the passages' },
			{ id: 1, type: 'message.delta', text: 'The dosage was 40mg.' },
		];
		const state = events.reduce(reduce, emptyStream());

		expect(state.text).toBe('The dosage was 40mg.');
		expect(state.reasoning).toBe('weighing the passages');
	});

	it('concatenates thinking fragments in arrival order', () => {
		const events = [
			{ id: 0, type: 'reasoning.delta', text: 'first ' },
			{ id: 1, type: 'reasoning.delta', text: 'second' },
		];
		expect(events.reduce(reduce, emptyStream()).reasoning).toBe('first second');
	});

	it('leaves reasoning null for a model that does not think aloud', () => {
		// Null is what keeps the disclosure off the screen entirely, so it
		// has to stay distinguishable from an empty thought.
		const state = [{ id: 0, type: 'message.delta', text: 'Answer.' }].reduce(reduce, emptyStream());
		expect(state.reasoning).toBeNull();
	});

	it('drops a replayed reasoning fragment after a reconnect', () => {
		// Same rule as the answer: applying a delta twice duplicates it.
		const state = [
			{ id: 0, type: 'reasoning.delta', text: 'thought' },
			{ id: 0, type: 'reasoning.delta', text: 'thought' },
		].reduce(reduce, emptyStream());
		expect(state.reasoning).toBe('thought');
	});
});

describe('tool calls in a stream', () => {
	const requested = {
		id: 'call-1',
		conversation_id: 'c',
		server_name: 'search',
		tool_name: 'web_search',
		arguments: { query: 'otters' },
		phase: 'requested',
		requested_at: '2026-11-02T00:00:00Z',
		expires_at: '2026-11-02T00:05:00Z',
	} as const;

	it('adds a requested call, and replaces it when it is decided', () => {
		// Later events carry the whole call again. Appending them would
		// show one call as two cards, the first still asking.
		let state = emptyStream();
		state = reduce(state, { type: 'tool.requested', id: 0, call: requested });
		state = reduce(state, {
			type: 'tool.decided',
			id: 1,
			call: { ...requested, phase: 'approved' },
		});

		expect(state.tools).toHaveLength(1);
		expect(state.tools[0]?.phase).toBe('approved');
	});

	it('marks a call running and collects its output', () => {
		let state = emptyStream();
		state = reduce(state, { type: 'tool.requested', id: 0, call: requested });
		state = reduce(state, { type: 'tool.running', id: 1, call_id: 'call-1' });
		expect(state.tools[0]?.phase).toBe('running');

		state = reduce(state, { type: 'tool.output', id: 2, call_id: 'call-1', text: '  1. otters' });
		expect(state.tools[0]?.output).toBe('  1. otters');
	});

	it('keeps calls in the order they were asked for', () => {
		let state = emptyStream();
		state = reduce(state, { type: 'tool.requested', id: 0, call: requested });
		state = reduce(state, {
			type: 'tool.requested',
			id: 1,
			call: { ...requested, id: 'call-2' },
		});
		state = reduce(state, {
			type: 'tool.completed',
			id: 2,
			call: { ...requested, phase: 'completed' },
		});

		expect(state.tools.map((call) => [call.id, call.phase])).toEqual([
			['call-1', 'completed'],
			['call-2', 'requested'],
		]);
	});

	it('does not change the call objects an earlier state holds', () => {
		// The transcript keeps states in `$state`; a write to an object it
		// already holds would not reach the screen.
		let state = emptyStream();
		state = reduce(state, { type: 'tool.requested', id: 0, call: requested });
		const before = state.tools[0];
		state = reduce(state, { type: 'tool.running', id: 1, call_id: 'call-1' });

		expect(before?.phase).toBe('requested');
	});
});
