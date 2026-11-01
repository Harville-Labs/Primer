import { asFailure, type Failure, problemOf } from './failure';

/**
 * Something the caller made in Lab and Lab keeps for them: steering
 * vectors on the Neurons tab, probes on the Anomalies tab.
 *
 * Lab holds each until its model changes, evicts the oldest past a cap,
 * and lists only the caller's own. So the list here is always the one Lab
 * last sent: making one reads it again rather than appending, and is never
 * left to disagree with what Lab kept.
 */
export interface OwnedOptions {
	/** The browser's fetch in the page; a recording one in tests. */
	fetch?: typeof globalThis.fetch;
}

export class OwnedList<T extends { id: string; label: string }, Request> {
	items = $state.raw<T[]>([]);
	creating = $state(false);
	failure = $state<Failure | null>(null);

	private readonly path: string;
	private readonly fetch: typeof globalThis.fetch;

	constructor(path: string, options: OwnedOptions = {}) {
		this.path = path;
		this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
	}

	async load(): Promise<void> {
		try {
			const response = await this.fetch(this.path);
			if (!response.ok) throw await problemOf(response);
			this.items = (await response.json()) as T[];
		} catch (failure) {
			this.failure = asFailure(failure);
		}
	}

	async create(request: Request): Promise<T | null> {
		if (this.creating) return null;
		this.creating = true;
		this.failure = null;
		try {
			const response = await this.fetch(this.path, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(request),
			});
			if (!response.ok) throw await problemOf(response);
			const made = (await response.json()) as T;
			await this.load();
			return made;
		} catch (failure) {
			this.failure = asFailure(failure);
			return null;
		} finally {
			this.creating = false;
		}
	}

	find(id: string | null): T | undefined {
		return this.items.find((item) => item.id === id);
	}
}
