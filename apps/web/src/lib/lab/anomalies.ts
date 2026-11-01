import type {
	ChatModel,
	DeploymentCapabilities,
	LabModelStatus,
	Outliers,
	ProbeReading,
	SequenceScore,
	Span,
	TokenScore,
} from '#lib/api/types.ts';
import { qualify } from '#lib/models.ts';
import { modelName } from './model';

/**
 * What the Anomalies tab can score with, and how its numbers are named.
 *
 * Plain functions over plain records, so the rules that decide what the
 * reader is offered - and what each number is called - can be asserted
 * without rendering anything.
 */

/** A chat endpoint, scored through Chat from the logprobs it returns. */
export interface EndpointSource {
	kind: 'endpoint';
	key: string;
	label: string;
	providerId: string;
	model: string;
	/** Whether it can score text it did not write: `supports_prompt_logprobs`. */
	scoresText: boolean;
}

/** Lab's resident model, which holds the whole distribution at every token. */
export interface LabSource {
	kind: 'lab';
	key: 'lab';
	label: string;
	scoresText: true;
}

export type Source = EndpointSource | LabSource;

export type ScoreMode = 'generate' | 'text';

/**
 * Whether this deployment has anything the Anomalies tab could score with.
 *
 * Chat being deployed is enough to offer the tab, without asking which of
 * its models return logprobs: the frame is loaded on every page, and
 * asking would put the inference endpoint's latency on all of them. The
 * tab itself says when no model declares logprobs, and where to declare it.
 */
export function canScore(capabilities: DeploymentCapabilities): boolean {
	return capabilities.lab_available || capabilities.chat_available;
}

/**
 * Every source the reader can pick, endpoints first.
 *
 * A model whose provider has not declared logprobs is left out rather than
 * offered and refused: Chat would refuse it before calling the endpoint,
 * and a picker full of entries that cannot work is a worse way to learn
 * that than a sentence saying so.
 */
export function sourcesFrom(models: ChatModel[], lab: LabModelStatus | null): Source[] {
	const endpoints: Source[] = models.flatMap((model) =>
		model.supports_logprobs && model.provider_id
			? [
					{
						kind: 'endpoint' as const,
						key: qualify(model),
						label: model.provider_name ? `${model.id} · ${model.provider_name}` : model.id,
						providerId: model.provider_id,
						model: model.id,
						scoresText: model.supports_prompt_logprobs,
					},
				]
			: [],
	);
	return lab
		? [
				...endpoints,
				{ kind: 'lab', key: 'lab', label: `${modelName(lab)} · Lab`, scoresText: true },
			]
		: endpoints;
}

/** How many logprobs an endpoint is asked for at each token. */
export const TOP_K = 20;

/**
 * What a score's entropies are, in words.
 *
 * Said on every entropy shown, because the two are not the same quantity:
 * from an endpoint's top 20 it is a lower bound, and a reader comparing it
 * with Lab's exact value would otherwise read the difference as the model.
 */
export function entropyBasis(score: SequenceScore): string {
	return score.estimate === 'full'
		? 'entropy over the whole vocabulary'
		: `entropy over the top ${score.top_k ?? TOP_K} only, a lower bound`;
}

/** A token's entropy in bits, whichever kind the score measured. */
export function entropyOf(token: TokenScore): number | null {
	return token.entropy_bits ?? token.truncated_entropy_bits ?? null;
}

/**
 * Each token's surprisal as a share of the most surprising one, for colour.
 *
 * Relative to this text rather than to a fixed scale: what is surprising
 * is a property of the text, and a fixed scale would draw a dull answer
 * as uniformly pale and a strange one as uniformly dark.
 */
export function surprisalShares(score: SequenceScore): number[] {
	const peak = Math.max(0, ...score.tokens.map((token) => token.surprisal_bits));
	return score.tokens.map((token) => (peak > 0 ? token.surprisal_bits / peak : 0));
}

export const SPAN_KINDS: Record<Span['kind'], { label: string; measures: string }> = {
	surprisal_spike: {
		label: 'Surprisal spike',
		measures: 'surprisal',
	},
	low_confidence: {
		label: 'Low confidence',
		measures: 'entropy',
	},
};

/** The tokens a span covers, joined as the text they came from. */
export function spanText(score: SequenceScore, span: Span): string {
	return score.tokens
		.slice(span.start, span.end)
		.map((token) => token.token)
		.join('');
}

/**
 * Each token's distance as a share of twice the threshold, for colour.
 *
 * Against the threshold rather than this text's peak, as surprisal is not:
 * the question a distance answers is whether the token is unlike ordinary
 * text, and a scale relative to the text would draw an ordinary answer's
 * most ordinary token as darkly as a strange one's. The threshold sits at
 * half strength, so everything darker than that is past it.
 */
export function distanceShares(outliers: Outliers): number[] {
	const full = 2 * outliers.threshold;
	return outliers.distances.map((distance) => (full > 0 ? Math.min(1, distance / full) : 1));
}

/** How many tokens are further from ordinary text than the threshold. */
export function pastThreshold(outliers: Outliers): number {
	return outliers.distances.filter((distance) => distance > outliers.threshold).length;
}

/** One row of the strip: its name, each cell's colour in -1..1, and each cell's number. */
export interface StripRow {
	name: string;
	values: number[];
	labels: string[];
}

/**
 * The strip's rows: surprisal, then whichever of Lab's detectors were read.
 *
 * Surprisal is the first row, drawn at the bottom, so the readings of the
 * residual stream sit above the score as layers sit above the output.
 * A probe is drawn signed: the negative ink is a token that reads like the
 * second set, the positive ink one that reads like the first.
 */
export function stripRows(
	score: SequenceScore,
	outliers: Outliers | null,
	probe: ProbeReading | null,
): StripRow[] {
	const rows: StripRow[] = [
		{
			name: 'bits',
			values: surprisalShares(score),
			labels: score.tokens.map((token) => token.surprisal_bits.toFixed(1)),
		},
	];
	if (outliers) {
		rows.push({
			name: `L${outliers.layer}`,
			values: distanceShares(outliers),
			labels: outliers.distances.map((distance) => (distance / outliers.threshold).toFixed(1)),
		});
	}
	if (probe) {
		rows.push({
			name: 'probe',
			values: probe.probabilities.map((p) => 2 * p - 1),
			labels: probe.probabilities.map((p) => p.toFixed(2)),
		});
	}
	return rows;
}
