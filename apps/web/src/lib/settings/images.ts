import type { ImageCapabilities, ImageOperation } from '#lib/api/types.ts';

/**
 * What a provider can be declared to do with images, read from a form.
 *
 * One parser for the add form and the change form, so the two cannot accept
 * different things. Chat validates the result again; this exists so that a
 * mistake is reported in words an administrator typed rather than as a
 * schema error about a field they never saw.
 */

/** Offered in this order, which is also the order of the checkboxes. */
export const IMAGE_OPERATIONS: readonly { operation: ImageOperation; label: string }[] = [
	{ operation: 'generate', label: 'Generate' },
	{ operation: 'edit', label: 'Edit' },
	{ operation: 'inpaint', label: 'Inpaint' },
];

const SIZE = /^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$/;

/** Commas or new lines, since a list of models is pasted as often as typed. */
function listed(value: FormDataEntryValue | null): string[] {
	return [
		...new Set(
			String(value ?? '')
				.split(/[,\n]/)
				.map((item) => item.trim())
				.filter(Boolean),
		),
	];
}

export type ImageDeclaration =
	| { ok: true; images: ImageCapabilities | null }
	| { ok: false; error: string };

/**
 * The declaration a form describes, or null when it names no image models.
 *
 * Null is "this provider makes no images", which is the add form's usual
 * answer; it is not an error to leave the image fields empty.
 */
export function imageDeclaration(form: FormData): ImageDeclaration {
	const models = listed(form.get('image_models'));
	if (models.length === 0) return { ok: true, images: null };

	const operations = IMAGE_OPERATIONS.map(({ operation }) => operation).filter(
		(operation) => form.get(`image_${operation}`) === 'true',
	);
	if (operations.length === 0) {
		return { ok: false, error: 'Choose what the image models can do: generate, edit, or inpaint.' };
	}
	const sizes = listed(form.get('image_sizes'));
	if (sizes.length === 0) {
		return { ok: false, error: 'Name at least one image size, such as 1024x1024.' };
	}
	const malformed = sizes.find((size) => !SIZE.test(size));
	if (malformed) {
		return {
			ok: false,
			error: `${malformed} is not a size. Write width x height, like 1024x1024.`,
		};
	}
	const max = Number(form.get('image_max') || 1);
	if (!Number.isInteger(max) || max < 1 || max > 10) {
		return { ok: false, error: 'Images per request must be a whole number from 1 to 10.' };
	}
	return {
		ok: true,
		images: {
			api: 'openai',
			models,
			operations,
			sizes,
			max_images: max,
			ask_for_base64: form.get('image_base64') === 'true',
			templates: [],
		},
	};
}
