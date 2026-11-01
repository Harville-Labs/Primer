import type {
	LabScored,
	LabScoreRequest,
	Outliers,
	Probe,
	ProbeReading,
	ProbeRequest,
	ScoredText,
	ScoreRequest,
	SequenceScore,
} from '#lib/api/types.ts';
import { type ScoreMode, type Source, TOP_K } from './anomalies';
import { asFailure, type Failure, problemOf } from './failure';
import { OwnedList, type OwnedOptions } from './owned.svelte';

/**
 * The Anomalies tab's state: which source, what to score, and the score.
 *
 * Here rather than in the page so it can be asserted against: the page only
 * renders what this says and calls what it offers.
 */

export interface ScorerOptions {
	/** The browser's fetch in the page; a recording one in tests. */
	fetch?: typeof globalThis.fetch;
	/** The longest answer Lab will generate, from its status. */
	labMaxNewTokens?: number;
}

/** A finished score, with what it was asked of. */
export interface Scored {
	source: Source;
	mode: ScoreMode;
	/** The answer that was generated, or the text as given. */
	text: string;
	score: SequenceScore;
	/** Each token's distance from ordinary text, when Lab was asked for it. */
	outliers: Outliers | null;
	/** A probe's reading at each token, when Lab was asked for one. */
	probe: ProbeReading | null;
}

/** How long an answer to ask for. Short: the point is to read it token by token. */
const ANSWER_TOKENS = 128;

export class Scorer {
	readonly sources: Source[];
	sourceKey = $state('');
	mode = $state<ScoreMode>('generate');
	text = $state('');
	/** Lab's detectors, sent only when Lab is the source: endpoints have no residual stream. */
	outlierLayer = $state<number | null>(null);
	probeId = $state<string | null>(null);

	running = $state(false);
	failure = $state<Failure | null>(null);
	// Raw: replaced whole when a score arrives, never edited.
	scored = $state.raw<Scored | null>(null);
	selected = $state(0);

	private readonly fetch: typeof globalThis.fetch;
	private readonly labMaxNewTokens: number;

	constructor(sources: Source[], options: ScorerOptions = {}) {
		this.sources = sources;
		this.sourceKey = sources[0]?.key ?? '';
		this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
		this.labMaxNewTokens = options.labMaxNewTokens ?? ANSWER_TOKENS;
	}

	get source(): Source | null {
		return this.sources.find((source) => source.key === this.sourceKey) ?? null;
	}

	/**
	 * The mode that will actually be sent.
	 *
	 * An endpoint that cannot score text it did not write is held to
	 * generating, whatever was picked for the source before it - the
	 * switch is hidden for it, and a hidden choice must not still apply.
	 */
	get effectiveMode(): ScoreMode {
		return this.source?.scoresText ? this.mode : 'generate';
	}

	async run(): Promise<void> {
		const source = this.source;
		const text = this.text;
		if (!source || !text.trim() || this.running) return;
		const mode = this.effectiveMode;
		this.running = true;
		this.failure = null;
		try {
			const response = await this.fetch(
				...(source.kind === 'lab'
					? request('/lab/score', {
							prompt: text,
							mode,
							chat: true,
							max_new_tokens: Math.min(ANSWER_TOKENS, this.labMaxNewTokens),
							outlier_layer: this.outlierLayer ?? undefined,
							probe_id: this.probeId ?? undefined,
						} satisfies LabScoreRequest)
					: request('/chat/score', {
							provider_id: source.providerId,
							model: source.model,
							mode,
							text,
							top_k: TOP_K,
							max_tokens: ANSWER_TOKENS,
						} satisfies ScoreRequest)),
			);
			if (!response.ok) throw await problemOf(response);
			const body = (await response.json()) as Pick<ScoredText, 'text' | 'score'> &
				Pick<Partial<LabScored>, 'outliers' | 'probe'>;
			this.scored = {
				source,
				mode,
				text: body.text,
				score: body.score,
				outliers: body.outliers ?? null,
				probe: body.probe ?? null,
			};
			this.selected = mostSurprising(body.score);
		} catch (failure) {
			this.failure = asFailure(failure);
		} finally {
			this.running = false;
		}
	}

	select(index: number): void {
		const count = this.scored?.score.tokens.length ?? 0;
		if (count > 0) this.selected = Math.max(0, Math.min(count - 1, index));
	}

	/** Returns whether the key moved the selection, so others pass through. */
	key(key: string): boolean {
		const moves: Record<string, number> = {
			ArrowLeft: this.selected - 1,
			ArrowRight: this.selected + 1,
			Home: 0,
			End: (this.scored?.score.tokens.length ?? 1) - 1,
		};
		if (!(key in moves)) return false;
		this.select(moves[key]);
		return true;
	}
}

/** The caller's probes, which a Lab score can read at every token. */
export class Probes extends OwnedList<Probe, ProbeRequest> {
	constructor(options: OwnedOptions = {}) {
		super('/lab/probes', options);
	}
}

/** Text is sent untrimmed: a leading space is a token of its own. */
function request(path: string, body: unknown): [string, RequestInit] {
	return [
		path,
		{
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
		},
	];
}

/** Opened on the token the reader most likely came to see. */
function mostSurprising(score: SequenceScore): number {
	let best = 0;
	score.tokens.forEach((token, index) => {
		if (token.surprisal_bits > score.tokens[best].surprisal_bits) best = index;
	});
	return best;
}
