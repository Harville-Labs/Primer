import type { RequestEvent } from '@sveltejs/kit';
import { describe, expect, it, vi } from 'vitest';

vi.mock('$app/env/private', () => ({
	PRIMER_CONTROL_URL: 'http://control.test',
	PRIMER_LAB_URL: 'http://lab.test',
}));

const { GET, POST } = await import('./+server');

/** Control says whether Lab is deployed; Lab answers the rest. */
function event(labAvailable: boolean, init: RequestInit = {}) {
	const sent: { url: string; method: string; body: unknown }[] = [];
	const fetch = (async (input: RequestInfo | URL, request: RequestInit = {}) => {
		const url = String(input);
		if (url.endsWith('/api/v1/capabilities')) {
			return Response.json({ lab_available: labAvailable });
		}
		sent.push({
			url,
			method: request.method ?? 'GET',
			body: request.body ? JSON.parse(String(request.body)) : null,
		});
		return Response.json(request.method === 'POST' ? { id: 'p' } : [], {
			status: request.method === 'POST' ? 201 : 200,
		});
	}) as typeof globalThis.fetch;
	const request = new Request('http://primer.test/lab/probes', init);
	return { sent, event: { request, fetch } as unknown as RequestEvent };
}

const TRAIN = { label: 'l', layer: 1, positive: ['a'], negative: ['b'], chat: false };

describe('Probes through Lab', () => {
	it('passes a training request on to Lab as it came', async () => {
		const { sent, event: trained } = event(true, {
			method: 'POST',
			body: JSON.stringify(TRAIN),
		});

		const response = await POST(trained as never);

		expect(response.status).toBe(200);
		expect(sent).toEqual([
			{ url: 'http://lab.test/api/v1/lab/probes', method: 'POST', body: TRAIN },
		]);
	});

	it('lists the caller’s probes from Lab', async () => {
		const { sent, event: listed } = event(true);

		expect(await (await GET(listed as never)).json()).toEqual([]);
		expect(sent.map((each) => each.url)).toEqual(['http://lab.test/api/v1/lab/probes']);
	});

	it('is not found on a deployment without Lab, and Lab is never asked', async () => {
		const { sent, event: absent } = event(false);

		await expect(GET(absent as never)).rejects.toMatchObject({ status: 404 });
		expect(sent).toEqual([]);
	});
});
