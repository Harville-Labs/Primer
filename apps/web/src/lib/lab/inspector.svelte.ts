import { asProblem } from '#lib/api/client.ts';
import type {
	ActivationSite,
	ActivationVector,
	InspectResponse,
	LabStatus,
} from '#lib/api/types.ts';
import { type Cell, moveSelection, type Signal } from './heatmap';

/**
 * The Neurons tab's state: one prompt, the reading it produced, and where
 * the reader is looking in it.
 *
 * Here rather than in the page so it can be asserted against: the page only
 * renders what this says and calls what it offers.
 */

/** Why the last thing asked of Lab did not happen, in Lab's own words. */
export interface Failure {
	title: string;
	detail: string;
}

export interface InspectorOptions {
	/** The browser's fetch in the page; a recording one in tests. */
	fetch?: typeof globalThis.fetch;
	/** How often to ask how many runs are ahead while one waits. */
	pollMs?: number;
}

/** A whole vector, with the cell and site it was read at. */
export interface ReadVector extends ActivationVector {
	stale: boolean;
}

export class Inspector {
	prompt = $state('');
	chat = $state(true);
	signal = $state<Signal>('lens');

	running = $state(false);
	/** Runs ahead of this one while it waits; null when not waiting or not known. */
	ahead = $state<number | null>(null);
	failure = $state<Failure | null>(null);

	// Raw rather than deep: a reading is tens of thousands of numbers that are
	// never edited, and a proxy per array would cost more than the render.
	// Replaced whole, never mutated - see "Working in the web app".
	reading = $state.raw<InspectResponse | null>(null);
	selected = $state<Cell>({ layer: 0, token: 0 });

	site = $state<ActivationSite>('resid_post');
	vector = $state.raw<ReadVector | null>(null);
	readingVector = $state(false);
	vectorFailure = $state<Failure | null>(null);

	/**
	 * What the current reading was asked with. The activation route
	 * re-reads a prompt, so it is sent this rather than whatever is in the
	 * composer now - otherwise editing the prompt after a run would make the
	 * vector belong to a different prompt than the grid beside it.
	 */
	private asked: { prompt: string; chat: boolean } | null = null;
	private readonly fetch: typeof globalThis.fetch;
	private readonly pollMs: number;

	constructor(options: InspectorOptions = {}) {
		this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
		this.pollMs = options.pollMs ?? 1000;
	}

	get shape(): { layers: number; tokens: number } {
		return {
			layers: this.reading?.layers.length ?? 0,
			tokens: this.reading?.tokens.length ?? 0,
		};
	}

	async run(neurons = 8, features = 8): Promise<void> {
		const prompt = this.prompt.trim();
		if (!prompt || this.running) return;
		this.running = true;
		this.failure = null;
		const asked = { prompt, chat: this.chat };
		const stopPolling = this.pollQueue();
		try {
			const reading = await this.post<InspectResponse>('/lab/inspect', {
				...asked,
				neurons,
				features,
			});
			this.asked = asked;
			this.reading = reading;
			this.vector = null;
			this.vectorFailure = null;
			// The last token at the last layer is where the model's next-token
			// prediction lives, which is the first thing anyone looks for.
			this.selected = { layer: reading.layers.length - 1, token: reading.tokens.length - 1 };
		} catch (failure) {
			this.failure = asFailure(failure);
		} finally {
			stopPolling();
			this.running = false;
			this.ahead = null;
		}
	}

	select(cell: Cell): void {
		const { layers, tokens } = this.shape;
		if (cell.layer < 0 || cell.layer >= layers || cell.token < 0 || cell.token >= tokens) return;
		this.selected = cell;
		if (this.vector) this.vector = { ...this.vector, stale: true };
	}

	/** Move by a key, and say whether the key was one this handled. */
	key(key: string): boolean {
		if (!this.reading) return false;
		const next = moveSelection(this.selected, key, this.shape);
		if (!next) return false;
		this.select(next);
		return true;
	}

	/** Read the whole vector at the selected cell. */
	async readVector(): Promise<void> {
		if (!this.reading || !this.asked || this.readingVector) return;
		const at = this.selected;
		const site = this.site;
		const model = this.reading.model;
		this.readingVector = true;
		this.vectorFailure = null;
		try {
			const vector = await this.post<ActivationVector>('/lab/activation', {
				...this.asked,
				layer: at.layer,
				token: at.token,
				site,
			});
			if (JSON.stringify(vector.model.reference) !== JSON.stringify(model.reference)) {
				// Lab was switched to another model since the grid was read, so
				// this vector is from a different network than the one on screen.
				this.vectorFailure = {
					title: 'The model changed',
					detail: 'Lab is running a different model than this reading. Run the prompt again.',
				};
				return;
			}
			const moved = this.selected.layer !== at.layer || this.selected.token !== at.token;
			this.vector = { ...vector, stale: moved || this.site !== site };
		} catch (failure) {
			this.vectorFailure = asFailure(failure);
		} finally {
			this.readingVector = false;
		}
	}

	private async post<T>(path: string, body: unknown): Promise<T> {
		const response = await this.fetch(path, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
		});
		if (!response.ok) throw await problemOf(response);
		return (await response.json()) as T;
	}

	/**
	 * Ask how many runs are ahead, until the run this is for comes back.
	 *
	 * Lab answers a run only when it is done, and a run behind others can
	 * wait a while; without this the screen is a spinner that looks the same
	 * whether it is first in line or tenth.
	 */
	private pollQueue(): () => void {
		let stopped = false;
		const tick = async () => {
			try {
				const response = await this.fetch('/lab/status');
				if (response.ok && !stopped) {
					const status = (await response.json()) as LabStatus;
					// This run is one of those counted, once it has been queued.
					if (!stopped) this.ahead = Math.max(0, status.queue_depth - 1);
				}
			} catch {
				// The count is a courtesy; a poll that fails says nothing about
				// the run, which reports its own failure.
			}
		};
		const timer = setInterval(tick, this.pollMs);
		return () => {
			stopped = true;
			clearInterval(timer);
		};
	}
}

class ProblemFailure extends Error {
	constructor(readonly failure: Failure) {
		super(failure.detail);
	}
}

async function problemOf(response: Response): Promise<ProblemFailure> {
	let body: unknown = null;
	try {
		body = await response.json();
	} catch {
		// Not JSON: asProblem below still says what the status was.
	}
	const problem = asProblem(body, response.status);
	return new ProblemFailure({
		title: problem.title,
		detail: problem.detail ?? problem.title,
	});
}

function asFailure(failure: unknown): Failure {
	if (failure instanceof ProblemFailure) return failure.failure;
	// The fetch itself failing means this page's own server did not answer,
	// which is a different thing from Lab not answering, and is said so.
	return {
		title: 'Primer did not answer',
		detail: 'The request did not reach Primer. Check your connection and try again.',
	};
}
