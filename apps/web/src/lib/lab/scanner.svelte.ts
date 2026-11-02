import type {
	LabModelStatus,
	LabScanned,
	PassagesResult,
	Probe,
	ScanFinding,
	ScanRun,
} from '#lib/api/types.ts';
import { asFailure, type Failure, problemOf } from './failure';
import { BATCH, batches, KEPT, rank } from './scan';

/**
 * Scanning one library for passages that read like injected instructions.
 *
 * The page lists the library's passages through Chat, which decides
 * whether the person may read it, and hands Lab their text a batch at a
 * time. Driven from here rather than queued in Lab: Lab then holds no
 * library and no long job of its own, and stopping a scan is not sending
 * the next batch. The cost is that closing the tab stops it too.
 */

export interface ScannerOptions {
	/** The browser's fetch in the page; a recording one in tests. */
	fetch?: typeof globalThis.fetch;
}

export interface ScanLibrary {
	id: string;
	name: string;
}

export type ScanPhase = 'idle' | 'listing' | 'scanning' | 'stopped' | 'done';

/** Lab refuses a passage longer than this, in characters, and would read only its start anyway. */
const MAX_TEXT = 20_000;

export class Scanner {
	phase = $state<ScanPhase>('idle');
	scanned = $state(0);
	/** Passages listed for this scan: what `scanned` counts towards. */
	listed = $state(0);
	/** Passages the library holds, which is more than `listed` past one listing. */
	total = $state(0);
	// Raw: each is replaced whole as batches come back, never written into.
	findings = $state.raw<ScanFinding[]>([]);
	library = $state.raw<ScanLibrary | null>(null);
	probe = $state.raw<Probe | null>(null);
	model = $state.raw<LabModelStatus | null>(null);
	failure = $state<Failure | null>(null);

	private readonly fetch: typeof globalThis.fetch;
	private controller: AbortController | null = null;

	constructor(options: ScannerOptions = {}) {
		this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
	}

	get running(): boolean {
		return this.phase === 'listing' || this.phase === 'scanning';
	}

	async start(library: ScanLibrary, probeId: string | null): Promise<void> {
		if (this.running) return;
		const controller = new AbortController();
		this.controller = controller;
		this.reset(library);
		this.phase = 'listing';
		try {
			const listing = (await this.answer(`/lab/passages/${encodeURIComponent(library.id)}`, {
				signal: controller.signal,
			})) as PassagesResult;
			const passages = listing.passages ?? [];
			this.listed = passages.length;
			this.total = listing.total ?? passages.length;
			this.phase = 'scanning';
			const byId = new Map(passages.map((passage) => [passage.chunk_id, passage]));
			for (const batch of batches(passages, BATCH)) {
				if (controller.signal.aborted) return;
				const scanned = (await this.answer('/lab/scan/batch', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({
						passages: batch.map((passage) => ({
							id: passage.chunk_id,
							text: passage.content.slice(0, MAX_TEXT),
						})),
						probe_id: probeId,
					}),
					signal: controller.signal,
				})) as LabScanned;
				this.model = scanned.model;
				this.probe = scanned.probe ?? null;
				const found = scanned.passages.flatMap((scored) => {
					const passage = byId.get(scored.id);
					return passage ? [{ passage, scored }] : [];
				});
				this.findings = rank([...this.findings, ...found], probeId !== null).slice(0, KEPT);
				this.scanned += batch.length;
			}
			this.phase = 'done';
		} catch (failure) {
			if (controller.signal.aborted) return;
			this.failure = asFailure(failure);
			this.phase = 'stopped';
		} finally {
			if (this.controller === controller) this.controller = null;
		}
	}

	/** Send no more batches. The one in flight is let go rather than waited for. */
	stop(): void {
		if (!this.running) return;
		this.controller?.abort();
		this.phase = 'stopped';
	}

	/** The scan on screen as a saveable run, or null when nothing has been scored. */
	run(): ScanRun | null {
		if (!this.library || !this.model || this.running) return null;
		return {
			kind: 'scan',
			request: {
				library_id: this.library.id,
				library_name: this.library.name,
				probe_id: this.probe?.id ?? null,
			},
			result: {
				model: this.model,
				probe: this.probe,
				scanned: this.scanned,
				total: this.total,
				findings: this.findings,
			},
		};
	}

	/** Show a saved scan as it was found. Nothing is scored again. */
	show(run: ScanRun): void {
		if (this.running || !run.result) return;
		this.reset({ id: run.request.library_id, name: run.request.library_name });
		this.model = run.result.model;
		this.probe = run.result.probe ?? null;
		this.scanned = run.result.scanned;
		this.listed = run.result.scanned;
		this.total = run.result.total;
		this.findings = run.result.findings;
		this.phase = 'done';
	}

	private reset(library: ScanLibrary): void {
		this.library = library;
		this.failure = null;
		this.findings = [];
		this.scanned = 0;
		this.listed = 0;
		this.total = 0;
		this.probe = null;
		this.model = null;
	}

	private async answer(url: string, init: RequestInit): Promise<unknown> {
		const response = await this.fetch(url, init);
		if (!response.ok) throw await problemOf(response);
		return response.json();
	}
}
