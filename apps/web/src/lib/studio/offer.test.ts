import { describe, expect, it } from 'vitest';
import { comfyProvider, image, openaiProvider } from './fixture';
import { lineage, offerOf, offersFor } from './offer';

describe('what Studio offers', () => {
	it('offers each declared model of an OpenAI-style endpoint, with no settings it did not declare', () => {
		const [offer] = offersFor([openaiProvider()], ['generate']);

		expect(offer).toMatchObject({
			model: 'gpt-image-1',
			maxCount: 4,
			sizes: ['1024x1024', '1536x1024'],
		});
		expect([offer.negativePrompt, offer.seed, offer.steps]).toEqual([false, false, false]);
	});

	it('offers a template only the inputs its operator bound, and one image a run', () => {
		const offers = offersFor([comfyProvider()], ['generate']);

		expect(offers.map((each) => each.model)).toEqual(['sdxl']);
		expect(offers[0]).toMatchObject({
			maxCount: 1,
			seed: true,
			steps: true,
			negativePrompt: false,
		});
	});

	it('leaves out what cannot do the operation', () => {
		const offers = offersFor([openaiProvider({ operations: ['edit'] }), comfyProvider()], ['edit']);

		expect(offers.map((each) => each.model)).toEqual(['gpt-image-1', 'sdxl-restyle']);
		expect(offersFor([openaiProvider({ operations: ['edit'] })], ['generate'])).toEqual([]);
	});

	it('finds the offer an image was made with, and none for an upload', () => {
		const offers = offersFor([comfyProvider()], ['generate']);

		expect(offerOf(offers, image('a'))?.key).toBe('p-comfy:sdxl');
		expect(offerOf(offers, image('b', { origin: 'upload', provenance: null }))).toBeNull();
	});
});

describe('lineage', () => {
	it('walks parents oldest first, stopping at one that is not loaded', () => {
		const root = image('root');
		const middle = image('middle', { parent_id: 'root' });
		const leaf = image('leaf', { parent_id: 'middle' });
		const orphan = image('orphan', { parent_id: 'deleted' });
		const known = new Map([root, middle, leaf, orphan].map((each) => [each.id, each]));

		expect(lineage(leaf, known).map((each) => each.id)).toEqual(['root', 'middle']);
		expect(lineage(orphan, known)).toEqual([]);
	});

	it('ends on a cycle rather than walking forever', () => {
		const a = image('a', { parent_id: 'b' });
		const b = image('b', { parent_id: 'a' });

		expect(lineage(a, new Map([a, b].map((each) => [each.id, each]))).length).toBeLessThanOrEqual(
			2,
		);
	});
});
