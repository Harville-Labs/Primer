import type { ImageOperation, ImageProvider, StudioImage } from '#lib/api/types.ts';

/**
 * What Studio may ask of each image endpoint, from what its operator declared.
 *
 * Built on the server from Chat's providers and handed to the page in place
 * of them: a ComfyUI template carries its whole workflow, which the browser
 * has no use for, and the settings a form shows are better decided once,
 * here, than by each control reading the declaration for itself.
 */

/** One thing a person can pick to make an image with: a model, or a template. */
export interface Offer {
	key: string;
	providerId: string;
	providerName: string;
	/** The model to name in the request. For ComfyUI, the template's name. */
	model: string;
	api: 'openai' | 'comfyui';
	/** Those of the operations a tab asked for that this one declares; never empty. */
	operations: ImageOperation[];
	sizes: string[];
	maxCount: number;
	/**
	 * Whether a setting may be sent at all. An OpenAI-style declaration says
	 * nothing about negative prompts, seeds or steps, and a hosted API refuses
	 * a field it does not know, so none is offered. A template takes each
	 * only when the operator bound it to an input.
	 */
	negativePrompt: boolean;
	seed: boolean;
	steps: boolean;
}

/** What can do any of `wanted`, each saying which of them it does. */
export function offersFor(providers: ImageProvider[], wanted: ImageOperation[]): Offer[] {
	const of = (declared: ImageOperation[]) => wanted.filter((each) => declared.includes(each));
	return providers.flatMap(({ provider_id, provider_name, capabilities }): Offer[] => {
		const common = {
			providerId: provider_id,
			providerName: provider_name,
			sizes: capabilities.sizes,
		};
		if (capabilities.api === 'comfyui') {
			return capabilities.templates
				.filter((template) => of(template.operations).length > 0)
				.map((template) => ({
					...common,
					key: `${provider_id}:${template.name}`,
					model: template.name,
					api: 'comfyui' as const,
					operations: of(template.operations),
					// ComfyUI runs a workflow once per request.
					maxCount: 1,
					negativePrompt: Boolean(template.bindings.negative_prompt),
					seed: Boolean(template.bindings.seed),
					steps: Boolean(template.bindings.steps),
				}));
		}
		const operations = of(capabilities.operations);
		if (operations.length === 0) return [];
		return capabilities.models.map((model) => ({
			...common,
			key: `${provider_id}:${model}`,
			model,
			api: 'openai' as const,
			operations,
			maxCount: capabilities.max_images,
			negativePrompt: false,
			seed: false,
			steps: false,
		}));
	});
}

/** The offer an image was made with, if this deployment still offers it. */
export function offerOf(offers: Offer[], image: StudioImage): Offer | null {
	const made = image.provenance;
	if (!made) return null;
	return (
		offers.find((each) => each.providerId === made.provider_id && each.model === made.model) ?? null
	);
}

/**
 * The images an image was made from, oldest first, as far as they are known.
 *
 * Stops at the first parent not in `known`: deleted, or on a page of
 * history not loaded yet. A cycle cannot come from Control, but a bound on
 * the walk costs nothing and means a bad row cannot hang the page.
 */
export function lineage(image: StudioImage, known: Map<string, StudioImage>): StudioImage[] {
	const chain: StudioImage[] = [];
	let parent = image.parent_id ? known.get(image.parent_id) : undefined;
	while (parent && chain.length < known.size) {
		chain.unshift(parent);
		parent = parent.parent_id ? known.get(parent.parent_id) : undefined;
	}
	return chain;
}
