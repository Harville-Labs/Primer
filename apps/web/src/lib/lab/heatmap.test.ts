import { describe, expect, it } from 'vitest';
import { reading } from './fixture';
import {
	cellColour,
	cellValues,
	featuresAt,
	lensAt,
	moveSelection,
	neuronsAt,
	normByLayer,
	summariseVector,
	topTokens,
	visibleToken,
} from './heatmap';

describe('colouring the grid', () => {
	it('colours the lens by how sure its top guess is', () => {
		expect(cellValues(reading(), 'lens')).toEqual([
			[0.2, 0.3],
			[0.5, 0.6],
			[0.9, 0.8],
		]);
	});

	it('keeps a neuron firing negative distinguishable from one firing positive', () => {
		const values = cellValues(reading(), 'neuron');
		// Scaled by the strongest anywhere (4), with the sign kept.
		expect(values[0]).toEqual([0.125, -0.5]);
		expect(values[1]).toEqual([0.25, 1]);
		expect(values[2]).toEqual([-0.25, 0.5]);
	});

	it('puts the norm on a log scale, so the early layers are not all the same pale cell', () => {
		const values = cellValues(reading(), 'norm');
		expect(values[0][0]).toBe(0);
		expect(values[2][1]).toBe(1);
		// On a linear scale layer 1's 10 would sit at 0.045; log puts it near the middle.
		expect(values[1][0]).toBeGreaterThan(0.3);
		expect(values[1][0]).toBeLessThan(0.6);
	});

	it('mixes a cell from the theme tokens, so both reading themes colour it', () => {
		expect(cellColour(0.5)).toBe(
			'color-mix(in oklab, var(--color-lab-positive) 50%, var(--color-card))',
		);
		expect(cellColour(-1)).toBe(
			'color-mix(in oklab, var(--color-lab-negative) 100%, var(--color-card))',
		);
		expect(cellColour(3)).toContain(' 100%');
	});
});

describe('moving the selection by keyboard', () => {
	const shape = { layers: 3, tokens: 2 };

	it('climbs toward the output with the up arrow', () => {
		expect(moveSelection({ layer: 0, token: 1 }, 'ArrowUp', shape)).toEqual({ layer: 1, token: 1 });
		expect(moveSelection({ layer: 1, token: 1 }, 'ArrowLeft', shape)).toEqual({
			layer: 1,
			token: 0,
		});
	});

	it('stops at the edges rather than wrapping or leaving the grid', () => {
		expect(moveSelection({ layer: 2, token: 1 }, 'ArrowUp', shape)).toEqual({ layer: 2, token: 1 });
		expect(moveSelection({ layer: 0, token: 0 }, 'PageDown', shape)).toEqual({
			layer: 0,
			token: 0,
		});
		expect(moveSelection({ layer: 0, token: 0 }, 'End', shape)).toEqual({ layer: 0, token: 1 });
	});

	it('leaves keys it does not own to the page', () => {
		expect(moveSelection({ layer: 0, token: 0 }, 'Tab', shape)).toBeNull();
	});
});

describe('reading a cell', () => {
	it('names the lens guesses from the vocabulary, with their probabilities', () => {
		expect(lensAt(reading(), { layer: 1, token: 0 })).toEqual([
			{ id: 2, text: ' world', probability: 0.5 },
			{ id: 3, text: '!', probability: 0.2 },
		]);
		expect(topTokens(reading())[2]).toEqual([' world', '!']);
	});

	it('lists the top neurons with their signed activations', () => {
		expect(neuronsAt(reading(), { layer: 0, token: 1 })).toEqual([
			{ index: 2, activation: -2 },
			{ index: 3, activation: 1 },
		]);
	});

	it('reads a layer with no SAE as no reading, not as no features firing', () => {
		const layers = reading().layers.map((layer, index) =>
			index === 1
				? { ...layer, feature_indices: [[9], [40, 7]], feature_activations: [[3], [-0.5, 0.25]] }
				: layer,
		);
		const withSaes = reading({ layers });
		expect(featuresAt(withSaes, { layer: 1, token: 1 })).toEqual([
			{ index: 40, activation: -0.5 },
			{ index: 7, activation: 0.25 },
		]);
		expect(featuresAt(withSaes, { layer: 0, token: 1 })).toBeNull();
	});

	it('follows one token up through every layer', () => {
		expect(normByLayer(reading(), 1)).toEqual([2, 20, 200]);
	});

	it('shows the whitespace that makes two tokens different', () => {
		expect(visibleToken(' world')).toBe('·world');
		expect(visibleToken('\n')).toBe('↵');
		expect(visibleToken('')).toBe('∅');
	});
});

describe('folding a whole vector', () => {
	it('keeps each bin’s standout dimension rather than averaging it away', () => {
		const values = [0, 0, 0, -8, 0, 0, 1, 2];
		const summary = summariseVector(values, 4);
		expect(summary.bins).toEqual([0, -1, 0, 0.25]);
		expect(summary).toMatchObject({ width: 8, low: -8, high: 2 });
		expect(summary.norm).toBeCloseTo(Math.sqrt(64 + 1 + 4));
	});

	it('never makes more bins than there are dimensions', () => {
		expect(summariseVector([1, -1], 384).bins).toEqual([1, -1]);
		expect(summariseVector([]).bins).toEqual([]);
	});
});
