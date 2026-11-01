import type {
	ChatModel,
	DeploymentCapabilities,
	LabModelStatus,
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
