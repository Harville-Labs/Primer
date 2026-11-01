import type { InspectResponse } from '#lib/api/types.ts';

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
