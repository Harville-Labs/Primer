import type {
	ChatModel,
	InspectResponse,
	LabModelStatus,
	Passage,
	Probe,
	SequenceScore,
} from '#lib/api/types.ts';

/**
 * A reading small enough to reason about by eye: three layers, two tokens,
 * two lens guesses and two neurons a cell.
 *
 * Shared by the heatmap's tests and the inspector's, so the two cannot
 * disagree about what a response looks like.
 */
export function reading(overrides: Partial<InspectResponse> = {}): InspectResponse {
	return {
		model: {
			reference: { source: 'local', path: 'tiny' },
			architecture: 'Qwen3ForCausalLM',
			commit: null,
			device: 'cpu',
			dtype: 'float32',
			num_layers: 3,
		},
		chat_template: false,
		tokens: [
			{ id: 1, text: 'hello', special: false },
			{ id: 2, text: ' world', special: false },
		],
		vocabulary: { '1': 'hello', '2': ' world', '3': '!' },
		hidden_size: 4,
		mlp_width: 8,
		layers: [
			{
				residual_norm: [1, 2],
				lens_tokens: [
					[3, 1],
					[1, 3],
				],
				lens_probabilities: [
					[0.2, 0.1],
					[0.3, 0.2],
				],
				neuron_indices: [
					[0, 1],
					[2, 3],
				],
				neuron_activations: [
					[0.5, 0.1],
					[-2, 1],
				],
			},
			{
				residual_norm: [10, 20],
				lens_tokens: [
					[2, 3],
					[3, 2],
				],
				lens_probabilities: [
					[0.5, 0.2],
					[0.6, 0.1],
				],
				neuron_indices: [
					[4, 5],
					[6, 7],
				],
				neuron_activations: [
					[1, 0.5],
					[4, -1],
				],
			},
			{
				residual_norm: [100, 200],
				lens_tokens: [
					[2, 1],
					[3, 1],
				],
				lens_probabilities: [
					[0.9, 0.05],
					[0.8, 0.1],
				],
				neuron_indices: [
					[1, 2],
					[3, 4],
				],
				neuron_activations: [
					[-1, 0.5],
					[2, 1],
				],
			},
		],
		...overrides,
	};
}

/** A chat model whose provider declared logprobs, but not prompt logprobs. */
export function chatModel(id: string, overrides: Partial<ChatModel> = {}): ChatModel {
	return {
		id,
		default: false,
		provider_id: 'p1',
		provider_name: 'Local vLLM',
		supports_logprobs: true,
		supports_prompt_logprobs: false,
		...overrides,
	};
}

export const RESIDENT: LabModelStatus = {
	architecture: 'Qwen3ForCausalLM',
	device: 'cpu',
	dtype: 'float32',
	num_layers: 2,
	reference: { source: 'local', path: 'qwen3-tiny' },
} as LabModelStatus;

/** A probe that did better than chance on held-out prompts. */
export const PROBE: Probe = {
	id: 'b'.repeat(32),
	label: 'formal',
	layer: 1,
	positive: 8,
	negative: 8,
	accuracy: 0.875,
	folds: 5,
	chance: 0.5,
	beats_chance: true,
};

export function sequence(
	surprisals: number[],
	overrides: Partial<SequenceScore> = {},
): SequenceScore {
	return {
		estimate: 'truncated',
		top_k: 20,
		perplexity: 2,
		spans: [],
		tokens: surprisals.map((surprisal_bits, index) => ({
			index,
			token: ` t${index}`,
			logprob: -surprisal_bits * Math.LN2,
			surprisal_bits,
			entropy_bits: null,
			truncated_entropy_bits: 1,
		})),
		...overrides,
	};
}

/** A library's passage, as Chat lists it for a scan. */
export function passage(id: string, page: number | null = 1): Passage {
	return {
		chunk_id: id,
		document_id: 'doc',
		filename: 'paper.pdf',
		ordinal: 0,
		content: `passage ${id}`,
		locator: page === null ? null : { page, section: null },
	};
}
