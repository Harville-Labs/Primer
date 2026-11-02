import type { InspectResponse } from '#lib/api/types.ts';

/**
 * The arithmetic behind the activation heatmap, kept out of the component
 * so it can be tested without rendering eighteen thousand cells.
 *
 * Everything here reads an `InspectResponse` and returns plain numbers or
 * plain records. Nothing holds state.
 */

/** What colours the cells. */
export type Signal = 'lens' | 'norm' | 'neuron';

export const SIGNALS: readonly { value: Signal; label: string; hint: string }[] = [
	{
		value: 'lens',
		label: 'Lens',
		hint: 'How sure the logit lens is of its top token at this layer.',
	},
	{
		value: 'norm',
		label: 'Norm',
		hint: 'The residual stream norm, on a log scale across the whole prompt.',
	},
	{
		value: 'neuron',
		label: 'Neuron',
		hint: 'The strongest MLP neuron at this cell, with its sign.',
	},
];

/** A cell, addressed the way the response indexes it. */
export interface Cell {
	layer: number;
	token: number;
}

/**
 * Every cell's value for a signal, as `[layer][token]` in -1..1.
 *
 * Lens and norm are 0..1; the neuron signal keeps its sign, because a neuron
 * firing strongly negative is as much a finding as one firing positive, and
 * folding the two together would hide which.
 *
 * The norm is on a log scale because the residual stream grows by orders of
 * magnitude from the embeddings to the last layer: on a linear scale every
 * layer but the last few is the same pale colour.
 */
export function cellValues(reading: InspectResponse, signal: Signal): number[][] {
	const layers = reading.layers;
	if (signal === 'lens') {
		return layers.map((layer) => layer.lens_probabilities.map((row) => row[0] ?? 0));
	}
	if (signal === 'norm') {
		const logs = layers.map((layer) => layer.residual_norm.map((norm) => Math.log1p(norm)));
		const flat = logs.flat();
		const low = Math.min(...flat);
		const span = Math.max(...flat) - low;
		return logs.map((row) => row.map((value) => (span > 0 ? (value - low) / span : 0)));
	}
	const strongest = layers.map((layer) => layer.neuron_activations.map((row) => row[0] ?? 0));
	const peak = Math.max(...strongest.flat().map(Math.abs));
	return strongest.map((row) => row.map((value) => (peak > 0 ? value / peak : 0)));
}

/**
 * The cell's background, mixed from theme tokens.
 *
 * Mixed against the card rather than given an opacity, so a cell is a solid
 * colour that reads the same over whatever is behind it, and the two ends of
 * the scale come from `app.css` - which is what keeps the heatmap readable
 * in both reading themes without a second palette here.
 */
export function cellColour(value: number): string {
	const ink = value < 0 ? '--color-lab-negative' : '--color-lab-positive';
	const share = Math.round(Math.min(1, Math.abs(value)) * 100);
	return `color-mix(in oklab, var(${ink}) ${share}%, var(--color-card))`;
}

/**
 * Whether text on a cell should switch to the light-on-dark ink.
 *
 * Past roughly half-way the mixed colour is closer to the scale's end than to
 * the card, and the card's own ink stops contrasting with it.
 */
export function isDeep(value: number): boolean {
	return Math.abs(value) > 0.55;
}

/**
 * The cell a key moves the selection to, or null for a key that is not ours.
 *
 * Up climbs toward the output, because the grid draws the last layer at the
 * top: the reader watches a prediction form as they go up.
 */
export function moveSelection(
	from: Cell,
	key: string,
	shape: { layers: number; tokens: number },
): Cell | null {
	const page = 8;
	const step: Record<string, Cell> = {
		ArrowUp: { layer: from.layer + 1, token: from.token },
		ArrowDown: { layer: from.layer - 1, token: from.token },
		ArrowLeft: { layer: from.layer, token: from.token - 1 },
		ArrowRight: { layer: from.layer, token: from.token + 1 },
		PageUp: { layer: from.layer + page, token: from.token },
		PageDown: { layer: from.layer - page, token: from.token },
		Home: { layer: from.layer, token: 0 },
		End: { layer: from.layer, token: shape.tokens - 1 },
	};
	const next = step[key];
	if (!next) return null;
	return {
		layer: clamp(next.layer, shape.layers - 1),
		token: clamp(next.token, shape.tokens - 1),
	};
}

function clamp(value: number, highest: number): number {
	return Math.max(0, Math.min(highest, value));
}

/**
 * A token's text with its whitespace made visible.
 *
 * `" world"` and `"world"` are different tokens, and a strip that rendered
 * both as "world" would hide the difference the reader came to see.
 */
export function visibleToken(text: string): string {
	if (text === '') return '∅';
	return text.replaceAll(' ', '·').replaceAll('\n', '↵').replaceAll('\t', '→');
}

/** One of the logit lens's guesses at a cell. */
export interface LensGuess {
	id: number;
	text: string;
	probability: number;
}

export function lensAt(reading: InspectResponse, at: Cell): LensGuess[] {
	const layer = reading.layers[at.layer];
	const ids = layer?.lens_tokens[at.token] ?? [];
	const probabilities = layer?.lens_probabilities[at.token] ?? [];
	return ids.map((id, rank) => ({
		id,
		text: reading.vocabulary[String(id)] ?? `#${id}`,
		probability: probabilities[rank] ?? 0,
	}));
}

/** The lens's top token at every cell, for printing inside the grid. */
export function topTokens(reading: InspectResponse): string[][] {
	return reading.layers.map((layer) =>
		layer.lens_tokens.map((row) => {
			const id = row[0];
			return id === undefined ? '' : (reading.vocabulary[String(id)] ?? `#${id}`);
		}),
	);
}

export interface NeuronReading {
	index: number;
	activation: number;
	/** The operator's name for a feature, where they gave it one. */
	label?: string;
}

export function neuronsAt(reading: InspectResponse, at: Cell): NeuronReading[] {
	const layer = reading.layers[at.layer];
	const indices = layer?.neuron_indices[at.token] ?? [];
	const activations = layer?.neuron_activations[at.token] ?? [];
	return indices.map((index, rank) => ({ index, activation: activations[rank] ?? 0 }));
}

/**
 * The strongest SAE features at a cell, or null where no SAE reads that
 * layer - which is not the same as an SAE that read it and found nothing.
 */
export function featuresAt(reading: InspectResponse, at: Cell): NeuronReading[] | null {
	const layer = reading.layers[at.layer];
	if (!layer?.feature_indices) return null;
	const indices = layer.feature_indices[at.token] ?? [];
	const activations = layer.feature_activations?.[at.token] ?? [];
	return indices.map((index, rank) => ({
		index,
		activation: activations[rank] ?? 0,
		label: layer.feature_labels?.[index],
	}));
}

/**
 * The operator's label for a feature, if this reading fired it and it has
 * one. A reading carries labels only for the features it found, so a
 * feature it did not fire has no label here even when the file names it.
 */
export function featureLabel(
	reading: InspectResponse,
	layer: number,
	index: number,
): string | undefined {
	return reading.layers[layer]?.feature_labels?.[index];
}

/** The residual norm at every layer for one token, bottom layer first. */
export function normByLayer(reading: InspectResponse, token: number): number[] {
	return reading.layers.map((layer) => layer.residual_norm[token] ?? 0);
}

/** What a whole activation vector looks like at a glance. */
export interface VectorSummary {
	width: number;
	norm: number;
	low: number;
	high: number;
	/** At most `bins` values in -1..1, each its bin's largest by magnitude. */
	bins: number[];
}

/**
 * Fold a vector into a strip a few hundred bins wide.
 *
 * A bin keeps its value of largest magnitude rather than its mean: the
 * reason to look at a whole vector is to find the few dimensions that stand
 * out, and a mean averages exactly those away.
 */
export function summariseVector(values: number[], bins = 384): VectorSummary {
	const width = values.length;
	if (width === 0) return { width, norm: 0, low: 0, high: 0, bins: [] };
	const peak = Math.max(...values.map(Math.abs)) || 1;
	const count = Math.min(bins, width);
	const folded = Array.from({ length: count }, (_, bin) => {
		const start = Math.floor((bin * width) / count);
		const end = Math.floor(((bin + 1) * width) / count);
		let kept = 0;
		for (let i = start; i < end; i++) {
			if (Math.abs(values[i]) > Math.abs(kept)) kept = values[i];
		}
		return kept / peak;
	});
	return {
		width,
		norm: Math.sqrt(values.reduce((sum, value) => sum + value * value, 0)),
		low: Math.min(...values),
		high: Math.max(...values),
		bins: folded,
	};
}
