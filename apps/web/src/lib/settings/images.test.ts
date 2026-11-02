import { describe, expect, it } from 'vitest';
import { imageDeclaration } from './images';

function form(fields: Record<string, string>): FormData {
	const data = new FormData();
	for (const [name, value] of Object.entries(fields)) data.set(name, value);
	return data;
}

describe('Reading an image declaration from a form', () => {
	it('declares nothing when no image model is named', () => {
		// The add form's usual case: most providers make no images.
		expect(imageDeclaration(form({ image_sizes: '1024x1024' }))).toEqual({
			ok: true,
			images: null,
		});
	});

	it('reads lists separated by commas or new lines, once each, in order', () => {
		const result = imageDeclaration(
			form({
				image_models: 'flux, sdxl\nflux',
				image_generate: 'true',
				image_inpaint: 'true',
				image_sizes: '1024x1024,512x512',
				image_max: '4',
				image_base64: 'true',
			}),
		);

		expect(result).toEqual({
			ok: true,
			images: {
				api: 'openai',
				models: ['flux', 'sdxl'],
				operations: ['generate', 'inpaint'],
				sizes: ['1024x1024', '512x512'],
				max_images: 4,
				ask_for_base64: true,
			},
		});
	});

	it('names a size that is not one', () => {
		const result = imageDeclaration(
			form({ image_models: 'flux', image_generate: 'true', image_sizes: '1024 by 1024' }),
		);

		expect(result).toEqual({ ok: false, error: expect.stringContaining('1024 by 1024') });
	});

	it('refuses models with nothing they can do', () => {
		const result = imageDeclaration(form({ image_models: 'flux', image_sizes: '1024x1024' }));

		expect(result.ok).toBe(false);
	});

	it('refuses more images per request than any provider may be asked for', () => {
		const result = imageDeclaration(
			form({
				image_models: 'flux',
				image_generate: 'true',
				image_sizes: '1024x1024',
				image_max: '11',
			}),
		);

		expect(result.ok).toBe(false);
	});
});
