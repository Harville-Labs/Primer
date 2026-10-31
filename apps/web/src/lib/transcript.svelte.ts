import { emptyStream, type RawEvent, reduce, type StreamState } from '#lib/api/sse.ts';
import type { MessageSummary } from '#lib/api/types.ts';
import { type Turn, turnsFrom } from '#lib/transcript.ts';

/**
 * The turns on screen, and the one still being written.
 *
 * The reactive half of `transcript.ts`, which holds the pure part: what a
 * turn is, and how a stored conversation is read back into turns. Split
 * that way because runes only exist in `.svelte.ts`, and everything that
 * does not need them is worth more as something a test can call directly.
 *
 * This exists as a module rather than as state in the page because of how
 * Svelte 5 reacts to change. A `$state` array hands out a proxy, and the
 * proxy creates a signal for a property the first time that property is
 * read - so a write to the *underlying* object afterwards never reaches the
 * screen. Folding a stream of events into a turn is exactly that shape of
 * code: hold the turn, mutate it as fragments arrive. Done that way the
 * answer streamed in, was stored, and never appeared; reloading the page
 * showed it, because the reload read it back from Chat.
 *
 * So an answer is written back through the array on every event, and the
 * running state is kept in a local that never went into `$state` at all.
 */

/** Somewhere to write one answer as it arrives. */
export interface Answer {
	/** What has arrived so far. The same value the transcript is showing. */
	readonly stream: StreamState;
	/** Fold in one event from the stream. */
	apply(event: RawEvent): void;
	/** End the turn as failed, keeping whatever text arrived before it. */
	fail(code: string, detail: string): void;
}

export class Transcript {
	#turns = $state<Turn[]>([]);

	/** The turns, oldest first. Read this to render them. */
	get turns(): Turn[] {
		return this.#turns;
	}

	get length(): number {
		return this.#turns.length;
	}

	get last(): Turn | undefined {
		return this.#turns.at(-1);
	}

	/** Show a stored conversation, replacing whatever is on screen. */
	open(messages: MessageSummary[]): void {
		this.#turns = turnsFrom(messages);
	}

	clear(): void {
		this.#turns = [];
	}

	/**
	 * Append a question that has been asked and not yet answered.
	 *
	 * The returned writer owns that turn's position for the life of the
	 * turn, which is safe because turns are only ever appended.
	 */
	ask(question: string): Answer {
		const index = this.#turns.length;
		//: Deliberately a local, and deliberately never handed to `$state`:
		//: everything the reducer produces is folded here and then copied
		//: into the array, rather than the array's copy being edited in
		//: place. See the note at the top of this file.
		let stream = emptyStream();
		this.#turns = [...this.#turns, { question, stream }];
		//: The transcript this turn belongs to. A conversation can be closed
		//: while its answer is still arriving, and without this the late
		//: events would be written into whatever thread replaced it, at the
		//: position this one used to occupy.
		const own = this.#turns;

		const write = () => {
			if (this.#turns !== own) return;
			own[index] = { question, stream };
		};

		return {
			get stream() {
				return stream;
			},
			apply(event: RawEvent) {
				stream = reduce(stream, event);
				write();
			},
			fail(code: string, detail: string) {
				// The text is kept. A half-written answer is the only thing
				// the reader can see about what went wrong.
				stream = { ...stream, error: { code, detail }, done: true };
				write();
			},
		};
	}
}
