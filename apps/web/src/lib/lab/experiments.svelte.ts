import type {
	AnomaliesRun,
	Experiment,
	ExperimentKind,
	ExperimentSummary,
	NeuronsRun,
	ScanRun,
} from '#lib/api/types.ts';
import { asFailure, type Failure, problemOf } from './failure';

/**
 * The caller's saved experiments of one kind: Neurons runs on the Neurons
 * tab, Anomalies runs on the Anomalies tab, scans on the Scan tab.
 *
 * Nothing is kept until the user saves. Lab keeps each saved experiment
 * for the operator's retention period and lists only the caller's own, so
 * the list here is always the one Lab last sent: every change reads it
 * again rather than editing it in place.
 */

export interface ExperimentsOptions {
	/** The browser's fetch in the page; a recording one in tests. */
	fetch?: typeof globalThis.fetch;
}

/** What a tab saves, by its kind. */
export type RunOf<K extends ExperimentKind> = K extends 'neurons'
	? NeuronsRun
	: K extends 'anomalies'
		? AnomaliesRun
		: ScanRun;

export class Experiments<K extends ExperimentKind> {
	// Raw: replaced whole from Lab's answer, never written into.
	items = $state.raw<ExperimentSummary[]>([]);
	busy = $state(false);
	failure = $state<Failure | null>(null);

	readonly kind: K;
	private readonly fetch: typeof globalThis.fetch;

	constructor(kind: K, options: ExperimentsOptions = {}) {
		this.kind = kind;
		this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
	}

	async load(): Promise<void> {
		await this.attempt(() => this.refresh());
	}

	/** Save a run under a title. Whether it was saved, so the form can clear. */
	async save(title: string, run: RunOf<K>): Promise<boolean> {
		return this.change(() => this.answer('/lab/experiments', json('POST', { title, run })));
	}

	async rename(id: string, title: string): Promise<boolean> {
		return this.change(() => this.answer(path(id), json('PATCH', { title })));
	}

	async remove(id: string): Promise<boolean> {
		return this.change(() => this.answer(path(id), { method: 'DELETE' }));
	}

	/**
	 * Restore an experiment and return it, for the page to show.
	 *
	 * Restored rather than only read: a Neurons run names steering vectors
	 * and an Anomalies run a probe, by ids that mean nothing until Lab holds
	 * them again. Lab refuses on any model but the one it was saved on.
	 */
	async open(id: string): Promise<Experiment | null> {
		let opened: Experiment | null = null;
		await this.attempt(async () => {
			opened = (await this.answer(`${path(id)}/restore`, { method: 'POST' })) as Experiment;
		});
		return opened;
	}

	private async change(call: () => Promise<unknown>): Promise<boolean> {
		return this.attempt(async () => {
			await call();
			await this.refresh();
		});
	}

	private async refresh(): Promise<void> {
		const all = (await this.answer('/lab/experiments')) as ExperimentSummary[];
		this.items = all.filter((each) => each.kind === this.kind);
	}

	/** One request at a time, so a double click cannot save the same run twice. */
	private async attempt(call: () => Promise<void>): Promise<boolean> {
		if (this.busy) return false;
		this.busy = true;
		this.failure = null;
		try {
			await call();
			return true;
		} catch (failure) {
			this.failure = asFailure(failure);
			return false;
		} finally {
			this.busy = false;
		}
	}

	private async answer(url: string, init?: RequestInit): Promise<unknown> {
		const response = await this.fetch(url, init);
		if (!response.ok) throw await problemOf(response);
		return response.status === 204 ? null : response.json();
	}
}

function path(id: string): string {
	return `/lab/experiments/${encodeURIComponent(id)}`;
}

function json(method: string, body: unknown): RequestInit {
	return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}
