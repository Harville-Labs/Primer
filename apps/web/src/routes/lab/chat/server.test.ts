import type { RequestEvent } from '@sveltejs/kit';
import { describe, expect, it, vi } from 'vitest';
import type { DeploymentCapabilities, SteeredChatRequest } from '#lib/api/types.ts';

vi.mock('$app/env/private', () => ({
	PRIMER_CONTROL_URL: 'http://control.test',
	PRIMER_LAB_URL: 'http://lab.test',
}));

const { POST } = await import('./+server');

const capabilities = { lab_available: true } as DeploymentCapabilities;

describe('the steered chat route', () => {
	it('passes the conversation to Lab’s chat route and its stream back unread', async () => {
		const asked: { url: string; body: unknown }[] = [];
		const frames = 'id: 0\nevent: token\ndata: {"id":0}\n\n';
		const fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
			const url = String(input);
			if (url.startsWith('http://control.test')) return Response.json(capabilities);
			asked.push({ url, body: JSON.parse(String(init.body)) });
			return new Response(frames, { headers: { 'Content-Type': 'text/event-stream' } });
		}) as typeof globalThis.fetch;
		const conversation: SteeredChatRequest = {
			messages: [{ role: 'user', content: 'hello' }],
			interventions: [{ site: 'mlp_neuron', layer: 1, index: 2, op: 'add', value: 4 }],
			max_new_tokens: 8,
		};
		const request = new Request('http://primer.test/lab/chat', {
			method: 'POST',
			headers: { 'x-forwarded-user': 'ada' },
			body: JSON.stringify(conversation),
		});

		const response = await POST({ request, fetch } as unknown as RequestEvent as never);

		expect(asked).toEqual([{ url: 'http://lab.test/api/v1/lab/chat', body: conversation }]);
		expect(await response.text()).toBe(frames);
	});
});
