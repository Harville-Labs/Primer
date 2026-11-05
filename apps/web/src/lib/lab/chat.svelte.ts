import type { ChatTurn, Intervention, SteeredChatRequest } from '#lib/api/types.ts';
import { readComparison } from './comparison';
import { emptyGeneration, type GenerationView } from './generation';

/**
 * A conversation with the resident model while edits steer it.
 *
 * Lab keeps no conversation, so this does: each exchange is what the person
 * said and the two replies to it. The steered reply is the one the next
 * turn sees as the assistant's, which makes the unsteered reply beside it
 * the model's own answer to the same steered conversation, not to a
 * separate unsteered one.
 */

export interface Exchange {
	key: string;
	said: string;
	/** The edits the reply was steered with, as they were when it was sent. */
	steering: Intervention[];
	view: GenerationView;
}

export interface SteeredChatOptions {
	fetch?: typeof globalThis.fetch;
}

export class SteeredChat {
	// Raw: an exchange is replaced whole on every event, never written into.
	exchanges = $state.raw<Exchange[]>([]);
	running = $state(false);

	private readonly fetch: typeof globalThis.fetch;
	private controller: AbortController | null = null;
	private next = 0;

	constructor(options: SteeredChatOptions = {}) {
		this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
	}

	/**
	 * The conversation as the next turn will send it.
	 *
	 * An exchange counts once Lab has finished it with a steered reply that
	 * says something. One that was stopped, failed, or ended its turn
	 * without a word is left out, so the person's words in it are not sent
	 * on unanswered, and the model is never shown a reply it did not write.
	 */
	get messages(): ChatTurn[] {
		return this.exchanges.flatMap((exchange) => {
			const reply = exchange.view.completed?.edited_text;
			if (!reply?.trim()) return [];
			return [
				{ role: 'user', content: exchange.said },
				{ role: 'assistant', content: reply },
			];
		});
	}

	/** Whether an exchange is part of what the model sees. */
	kept(exchange: Exchange): boolean {
		return Boolean(exchange.view.completed?.edited_text?.trim());
	}

	async send(said: string, interventions: Intervention[], maxNewTokens: number): Promise<void> {
		const content = said.trim();
		if (!content) return;
		// One reply at a time: a second turn sent while the first is still
		// writing would be answered without it.
		this.stop();
		const asked: SteeredChatRequest = {
			messages: [...this.messages, { role: 'user', content }],
			interventions,
			max_new_tokens: maxNewTokens,
		};
		const key = `exchange-${this.next++}`;
		this.exchanges = [
			...this.exchanges,
			{ key, said: content, steering: interventions, view: emptyGeneration() },
		];
		const controller = new AbortController();
		this.controller = controller;
		this.running = true;
		try {
			await readComparison(this.fetch, '/lab/chat', asked, controller.signal, (view) => {
				this.exchanges = this.exchanges.map((each) =>
					each.key === key ? { ...each, view } : each,
				);
			});
		} finally {
			if (this.controller === controller) {
				this.controller = null;
				this.running = false;
			}
		}
	}

	/** Stop reading. Lab notices the stream has gone and stops between tokens. */
	stop(): void {
		this.controller?.abort();
		this.controller = null;
		this.running = false;
	}

	/** Start the conversation again. */
	clear(): void {
		this.stop();
		this.exchanges = [];
	}
}
