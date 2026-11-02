import { describe, expect, it } from 'vitest';
import { comfyProvider, image, openaiProvider, primer, stream } from './fixture';
import { Generator } from './generator.svelte';
import { offersFor } from './offer';

const EDITS = ['edit', 'inpaint'] as const;
// The hosted model edits and inpaints; the restyle template only edits.
const OFFERS = offersFor(
	[openaiProvider({ operations: ['generate', 'edit', 'inpaint'] }), comfyProvider()],
	[...EDITS],
);

describe('an edit', () => {
	it('offers what can edit, each with the ways it can', () => {
		expect(OFFERS.map((each) => [each.model, each.operations])).toEqual([
			['gpt-image-1', ['edit', 'inpaint']],
			['sdxl-restyle', ['edit']],
		]);
	});

	it('sends the source image, and no mask for an edit by instruction', async () => {
		const { fetch, asked } = primer(() => stream({ type: 'done', images: [image('child')] }));
		const generator = new Generator(OFFERS, [], { fetch });
		generator.edit(image('source'));
		generator.prompt = 'make the sky stormy';

		await generator.run('ignored');

		expect(asked[0].body).toMatchObject({
			operation: 'edit',
			source_id: 'source',
			mask: null,
		});
	});

	it('cannot be sent without an image to change', () => {
		const generator = new Generator(OFFERS, []);
		generator.prompt = 'make the sky stormy';

		expect(generator.request).toBeNull();
	});

	it('inpaints only with a painted mask, and sends it', async () => {
		const { fetch, asked } = primer(() => stream({ type: 'done', images: [] }));
		const generator = new Generator(OFFERS, [], { fetch });
		generator.edit(image('source'));
		generator.prompt = 'a red door';
		generator.operation = 'inpaint';

		await generator.run(null);
		expect(asked).toEqual([]);

		await generator.run('bWFzaw==');
		expect(asked[0].body).toMatchObject({ operation: 'inpaint', mask: 'bWFzaw==' });
	});

	it('falls back to an edit by instruction for a template that cannot inpaint', () => {
		const generator = new Generator(OFFERS, []);
		generator.operation = 'inpaint';

		generator.pick('p-comfy:sdxl-restyle');

		expect(generator.operation).toBe('edit');
	});

	it('adds an uploaded image to the history and edits it', async () => {
		const uploaded = image('upload', { origin: 'upload', provenance: null });
		const { fetch, asked } = primer(() => Response.json(uploaded));
		const generator = new Generator(OFFERS, [image('old')], { fetch });

		await generator.upload(new File(['png'], 'photo.png', { type: 'image/png' }));

		expect(asked[0]).toMatchObject({ path: '/studio/images', method: 'POST' });
		expect((asked[0].body as FormData).get('file')).toBeInstanceOf(File);
		expect(generator.source).toEqual(uploaded);
		expect(generator.history.map((each) => each.id)).toEqual(['upload', 'old']);
	});

	it('stops editing an image that was deleted', async () => {
		const { fetch } = primer(() => new Response(null, { status: 204 }));
		const source = image('source');
		const generator = new Generator(OFFERS, [source], { fetch });
		generator.edit(source);

		await generator.remove(source);

		expect(generator.source).toBeNull();
	});
});
