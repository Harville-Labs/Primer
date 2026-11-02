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

/** An SSE body, one event a frame, the way Control writes it. */
export function stream(...events: object[]): Response {
	const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');
	return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
}

/** The page's own endpoints, answering from a list and keeping every request. */
export function primer(...answers: (() => Response)[]) {
	const asked: { path: string; method: string; body: unknown }[] = [];
	const fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
		asked.push({
			path: String(input),
			method: init.method ?? 'GET',
			body: typeof init.body === 'string' ? JSON.parse(init.body) : (init.body ?? null),
		});
		const answer = answers.shift();
		if (!answer) throw new Error(`nothing answers ${String(input)}`);
		return answer();
	}) as typeof globalThis.fetch;
	return { fetch, asked };
}
