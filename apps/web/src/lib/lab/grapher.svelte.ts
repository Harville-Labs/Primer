import type { AttributionGraph, GraphRequest } from '#lib/api/types.ts';
import { asFailure, type Failure, problemOf } from './failure';

/**
 * The Graph pane's state: what to trace, the graph that came back, and
 * which node is being read.
 *
 * The thresholds go to Lab, which prunes before answering; the influence
 * cutoff stays here and only hides, so moving it redraws without another
 * build that may take minutes.
 */

export interface GrapherOptions {
	/** The browser's fetch in the page; a recording one in tests. */
	fetch?: typeof globalThis.fetch;
}

/** Lab's defaults, which `GraphRequest` documents. */
export const NODE_THRESHOLD = 0.8;
export const EDGE_THRESHOLD = 0.98;

export class Grapher {
	prompt = $state('');
	/** One token to explain. Empty explains the likeliest ones instead. */
	target = $state('');
	nodeThreshold = $state(NODE_THRESHOLD);
	edgeThreshold = $state(EDGE_THRESHOLD);
	/** Features shown, by the same cumulative influence the build pruned with. */
	influence = $state(NODE_THRESHOLD);

	running = $state(false);
	failure = $state<Failure | null>(null);
	// Raw: replaced whole when a build arrives, never edited.
	built = $state.raw<AttributionGraph | null>(null);
	selected = $state<string | null>(null);

	private readonly fetch: typeof globalThis.fetch;

	constructor(options: GrapherOptions = {}) {
		this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
	}

	async build(): Promise<void> {
		if (this.running || !this.prompt) return;
		// Sent exactly as typed: a leading space is part of the token.
		const body: GraphRequest = {
			prompt: this.prompt,
			target: this.target === '' ? null : this.target,
			node_threshold: this.nodeThreshold,
			edge_threshold: this.edgeThreshold,
		};
		this.running = true;
		this.failure = null;
		try {
			const response = await this.fetch('/lab/graph', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(body),
			});
			if (!response.ok) throw await problemOf(response);
			this.built = (await response.json()) as AttributionGraph;
			// Everything the build kept is shown at first; hiding is the reader's to do.
			this.influence = body.node_threshold ?? NODE_THRESHOLD;
			this.selected = this.strongestLogit();
		} catch (failure) {
			this.failure = asFailure(failure);
		} finally {
			this.running = false;
		}
	}

	select(id: string | null): void {
		this.selected = this.selected === id ? null : id;
	}

	/** Open on the answer: the likeliest predicted token, whose causes are the point. */
	private strongestLogit(): string | null {
		const logits = (this.built?.graph.nodes ?? []).filter((node) => node.kind === 'logit');
		logits.sort((a, b) => (b.probability ?? 0) - (a.probability ?? 0));
		return logits[0]?.id ?? null;
	}
}
