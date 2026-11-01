import { parseEvents } from '#lib/api/sse.ts';
import type { Intervention, SteeringVector, SteeringVectorRequest } from '#lib/api/types.ts';
import { asFailure, type Failure, problemOf } from './failure';
import { emptyGeneration, foldGeneration, type GenerationView, OP_DEFAULTS } from './generation';

/**
 * The Neurons tab's experiments: the edits to make, the generation that
 * compares an edited answer with the model's own, and the steering vectors
 * the edits can add.
 *
 * Every change replaces the object it changes rather than writing into it.
 * An edit is in `$state` from the moment it is added, and a write to the
 * object behind the proxy would not reach the screen - see "Working in the
 * web app".
 */

/** An edit as it is being made, with a key the list can track it by. */
export interface DraftEdit extends Intervention {
	key: string;
}

export class Edits {
	items = $state<DraftEdit[]>([]);
	/**
	 * The edit whose controls are showing. The list's rows are its reorder
	 * handles and cannot hold controls of their own, so one edit at a time
	 * is open below them.
	 */
	active = $state<string | null>(null);
	private next = 0;

	get current(): DraftEdit | undefined {
		return this.items.find((item) => item.key === this.active);
	}

	/** Add an edit at the end, where it is applied last at its site. */
	add(edit: Omit<Intervention, 'value' | 'positions'> & Partial<Intervention>): string {
		const key = `edit-${this.next++}`;
		this.items = [...this.items, { value: OP_DEFAULTS[edit.op], positions: 'all', ...edit, key }];
		// Opened, because the next thing after adding an edit is setting it.
		this.active = key;
		return key;
	}

	/**
	 * Change one edit's fields.
	 *
	 * A new operation brings its own starting value unless one is given:
	 * a scale of 0 left over from a clamp would quietly be a zero.
	 */
	update(key: string, change: Partial<Intervention>): void {
		const index = this.items.findIndex((item) => item.key === key);
		if (index < 0) return;
		const current = this.items[index];
		const value =
			change.op && change.op !== current.op && change.value === undefined
				? OP_DEFAULTS[change.op]
				: (change.value ?? current.value);
		this.items[index] = { ...$state.snapshot(current), ...change, value, key };
	}

	/** Remove an edit, opening its neighbour if it was the open one. */
	remove(key: string): void {
		const index = this.items.findIndex((item) => item.key === key);
		if (index < 0) return;
		this.items = this.items.filter((item) => item.key !== key);
		if (this.active === key) {
			this.active = (this.items[index] ?? this.items[index - 1])?.key ?? null;
		}
	}

	/** Take the list's new order, as the reorder list hands it back. */
	reorder(items: DraftEdit[]): void {
		const known = new Set(this.items.map((item) => item.key));
		const given = new Set(items.map((item) => item.key));
		// Compared as sets of the same size as the list, so an order that
		// repeats one edit in place of another is refused rather than taken.
		if (items.length !== known.size || given.size !== known.size) return;
		if (![...given].every((key) => known.has(key))) return;
		this.items = items.map((item) => ({ ...$state.snapshot(item) }));
	}

	/** The edits as Lab reads them. */
	get interventions(): Intervention[] {
		return this.items.map(({ key: _key, ...edit }) => $state.snapshot(edit) as Intervention);
	}
}

export interface GeneratorOptions {
	fetch?: typeof globalThis.fetch;
}

export interface GenerateAsk {
	prompt: string;
	chat: boolean;
	interventions: Intervention[];
	maxNewTokens: number;
	/** A token to watch the logit of at the first answer position. */
	target: string;
}

export class Generator {
	running = $state(false);
	// Raw: replaced whole on every event, never written into.
	view = $state.raw<GenerationView>(emptyGeneration());

	private readonly fetch: typeof globalThis.fetch;
	private controller: AbortController | null = null;

	constructor(options: GeneratorOptions = {}) {
		this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
	}

	async run(ask: GenerateAsk): Promise<void> {
		const prompt = ask.prompt.trim();
		if (!prompt) return;
		// A new run replaces one in progress. Leaving the old one reading
		// would interleave two runs' tokens into one view.
		this.stop();
		const controller = new AbortController();
		this.controller = controller;
		this.running = true;
		this.view = emptyGeneration();
		const target = ask.target.trim() ? ask.target : null;
		try {
			const response = await this.fetch('/lab/generate', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					prompt,
					chat: ask.chat,
					interventions: ask.interventions,
					max_new_tokens: ask.maxNewTokens,
					target,
				}),
				signal: controller.signal,
			});
			if (!response.ok) throw await problemOf(response);
			if (!response.body) throw new Error('The generation came back without a stream.');
			for await (const event of parseEvents(response.body)) {
				if (controller.signal.aborted) return;
				this.view = foldGeneration(this.view, event);
			}
			if (!this.view.completed && !this.view.failure) {
				this.view = {
					...this.view,
					failure: {
						title: 'The generation stopped',
						detail: 'The stream ended before Lab finished. Run it again.',
					},
				};
			}
		} catch (failure) {
			if (controller.signal.aborted) return;
			this.view = { ...this.view, ahead: null, failure: asFailure(failure) };
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
}

export class Steering {
	vectors = $state.raw<SteeringVector[]>([]);
	creating = $state(false);
	failure = $state<Failure | null>(null);

	private readonly fetch: typeof globalThis.fetch;

	constructor(options: GeneratorOptions = {}) {
		this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
	}

	/** The caller's vectors. Lab keeps them until its model changes. */
	async load(): Promise<void> {
		try {
			const response = await this.fetch('/lab/vectors');
			if (!response.ok) throw await problemOf(response);
			this.vectors = (await response.json()) as SteeringVector[];
		} catch (failure) {
			this.failure = asFailure(failure);
		}
	}

	async create(request: SteeringVectorRequest): Promise<SteeringVector | null> {
		if (this.creating) return null;
		this.creating = true;
		this.failure = null;
		try {
			const response = await this.fetch('/lab/vectors', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(request),
			});
			if (!response.ok) throw await problemOf(response);
			const vector = (await response.json()) as SteeringVector;
			// Lab evicts the oldest past its cap, so the list is read again
			// rather than appended to and left to disagree with it.
			await this.load();
			return vector;
		} catch (failure) {
			this.failure = asFailure(failure);
			return null;
		} finally {
			this.creating = false;
		}
	}

	label(id: string): string | undefined {
		return this.vectors.find((vector) => vector.id === id)?.label;
	}
}
