import { emptyStream, type StreamState } from '#lib/api/sse.ts';
import type { MessageSummary } from '#lib/api/types.ts';

/**
 * Reading a stored conversation back onto the screen.
 *
 * What is stored is a flat sequence of messages; what is shown is a turn -
 * a question with the answer to it. The pairing happens here rather than in
 * the page so that the awkward cases have somewhere to be tested.
 */

export interface Turn {
	question: string;
	stream: StreamState;
}

function answered(message: MessageSummary): StreamState {
	return {
		...emptyStream(),
		messageId: message.id,
		conversationId: message.conversation_id,
		text: message.content,
		// Stored with the answer, so reopening a thread shows the thinking
		// that produced it rather than only the conclusion.
		reasoning: message.reasoning ?? null,
		citations: message.citations,
		message,
		// A failed answer is still failed when it is read back, and the half
		// it managed to write is still shown. Storing that half was the whole
		// point of storing it.
		error:
			message.state === 'failed'
				? { code: message.error_code ?? 'generation_failed', detail: null }
				: null,
		done: true,
	};
}

/**
 * Turns, in the order they were written.
 *
 * An answer with no question before it gets an empty one rather than being
 * dropped. It is a real thing that was said, and hiding it would make the
 * transcript disagree with what is stored - which is the one thing a
 * transcript must not do.
 */
export function turnsFrom(messages: MessageSummary[]): Turn[] {
	const turns: Turn[] = [];
	for (const message of messages) {
		if (message.role === 'user') {
			turns.push({ question: message.content, stream: { ...emptyStream(), done: true } });
			continue;
		}
		const last = turns.at(-1);
		// Only a turn still waiting for its answer takes this one. Two
		// answers in a row are two turns, not one answer overwriting another.
		if (last && last.stream.message === null && last.stream.error === null) {
			last.stream = answered(message);
		} else {
			turns.push({ question: '', stream: answered(message) });
		}
	}
	return turns;
}

/**
 * Where the model answering the conversation changed, turn by turn.
 *
 * The name to say above a turn, or null for a turn answered by the same
 * model as the turn before it. Read from what each answer recorded rather
 * than from the picker, because the picker says what the *next* question
 * will be sent to - a transcript read back a week later has no picker, and
 * a conversation where someone switched models halfway is exactly the one
 * this is for.
 *
 * The first model is never announced. A conversation that only ever used
 * one model has not switched to it, and saying so would put a line at the
 * top of every transcript in a deployment that offers a single model.
 */
export function modelChanges(turns: Turn[]): (string | null)[] {
	let current: string | null = null;
	return turns.map((turn) => {
		const model = turn.stream.message?.provider_model ?? null;
		// An answer that failed or is still arriving records nothing yet.
		// Silence is right: it has not been answered by a different model,
		// it has not been answered.
		if (model === null || model === current) return null;
		const switched = current === null ? null : model;
		current = model;
		return switched;
	});
}
