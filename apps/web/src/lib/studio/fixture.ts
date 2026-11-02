import type { ImageProvider, StudioImage, StudioProvenance } from '#lib/api/types.ts';

/** Studio's shapes as the services send them, for tests. */

export function openaiProvider(
	overrides: Partial<ImageProvider['capabilities']> = {},
): ImageProvider {
	return {
		provider_id: 'p-openai',
		provider_name: 'Hosted',
		capabilities: {
			api: 'openai',
			ask_for_base64: false,
			max_images: 4,
			models: ['gpt-image-1'],
			operations: ['generate', 'edit'],
			sizes: ['1024x1024', '1536x1024'],
			templates: [],
			...overrides,
		},
	};
}

export function comfyProvider(): ImageProvider {
	const input = (node: string, name: string) => ({ node, input: name });
	return {
		provider_id: 'p-comfy',
		provider_name: 'ComfyUI',
		capabilities: {
			api: 'comfyui',
			ask_for_base64: false,
			max_images: 1,
			models: [],
			operations: [],
			sizes: ['1024x1024'],
			templates: [
				{
					name: 'sdxl',
					operations: ['generate'],
					workflow: { '6': { class_type: 'CLIPTextEncode' } },
					bindings: {
						prompt: input('6', 'text'),
						seed: input('3', 'seed'),
						steps: input('3', 'steps'),
						output: '9',
					},
				},
				{
					name: 'sdxl-restyle',
					operations: ['edit'],
					workflow: {},
					bindings: { prompt: input('6', 'text'), image: input('10', 'image'), output: '9' },
				},
			],
		},
	};
}

export function image(id: string, overrides: Partial<StudioImage> = {}): StudioImage {
	return {
		id,
		origin: 'generated',
		media_type: 'image/png',
		width: 1024,
		height: 1024,
		byte_size: 2048,
		created_at: '2026-11-01T12:00:00Z',
		parent_id: null,
		provenance: provenance(),
		...overrides,
	};
}

export function provenance(overrides: Partial<StudioProvenance> = {}): StudioProvenance {
	return {
		provider_id: 'p-comfy',
		model: 'sdxl',
		operation: 'generate',
		prompt: 'a lighthouse at dusk',
		negative_prompt: null,
		size: '1024x1024',
		seed: 7,
		steps: 30,
		...overrides,
	};
}
