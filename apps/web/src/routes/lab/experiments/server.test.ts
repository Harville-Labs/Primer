import type { RequestEvent } from '@sveltejs/kit';
import { describe, expect, it, vi } from 'vitest';

vi.mock('$app/env/private', () => ({
	PRIMER_CONTROL_URL: 'http://control.test',
	PRIMER_LAB_URL: 'http://lab.test',
}));

const listed = await import('./+server');
const one = await import('./[id]/+server');
const restore = await import('./[id]/restore/+server');

const SUMMARY = { id: 'e1', kind: 'neurons', title: 'Ablation' };

/** Control says whether Lab is deployed; Lab answers the rest as it would. */
function event(labAvailable: boolean, init: RequestInit = {}, id = 'e1') {
	const sent: { url: string; method: string; body: unknown }[] = [];
	const fetch = (async (input: RequestInfo | URL, request: RequestInit = {}) => {
		const url = String(input);
		if (url.endsWith('/api/v1/capabilities')) {
			return Response.json({ lab_available: labAvailable });
		}
		const method = request.method ?? 'GET';
		sent.push({ url, method, body: request.body ? JSON.parse(String(request.body)) : null });
		if (method === 'DELETE') return new Response(null, { status: 204 });
		return Response.json(method === 'GET' && url.endsWith('/experiments') ? [SUMMARY] : SUMMARY);
	}) as typeof globalThis.fetch;
	const request = new Request('http://primer.test/lab/experiments', init);
	return { sent, event: { request, fetch, params: { id } } as unknown as RequestEvent };
}

const SAVE = {
	title: 'Ablation',
	run: { kind: 'neurons', request: { prompt: 'hello', interventions: [] } },
};

describe('Saved experiments through Lab', () => {
	it('passes a save on to Lab as it came', async () => {
		const { sent, event: saved } = event(true, { method: 'POST', body: JSON.stringify(SAVE) });

		await listed.POST(saved as never);

		expect(sent).toEqual([
			{ url: 'http://lab.test/api/v1/lab/experiments', method: 'POST', body: SAVE },
		]);
	});

	it('lists the caller’s experiments from Lab', async () => {
		const { event: asked } = event(true);

		expect(await (await listed.GET(asked as never)).json()).toEqual([SUMMARY]);
	});

	it('renames with a PATCH to the one experiment', async () => {
		const { sent, event: renamed } = event(true, {
			method: 'PATCH',
			body: JSON.stringify({ title: 'Better' }),
		});

		await one.PATCH(renamed as never);

		expect(sent).toEqual([
			{
				url: 'http://lab.test/api/v1/lab/experiments/e1',
				method: 'PATCH',
				body: { title: 'Better' },
			},
		]);
	});

	it('answers a delete with no content, as Lab did', async () => {
		const { sent, event: deleted } = event(true, { method: 'DELETE' });

		const response = await one.DELETE(deleted as never);

		expect(response.status).toBe(204);
		expect(await response.text()).toBe('');
		expect(sent.map((each) => each.method)).toEqual(['DELETE']);
	});

	it('restores through Lab’s restore route', async () => {
		const { sent, event: restored } = event(true, { method: 'POST' });

		expect(await (await restore.POST(restored as never)).json()).toEqual(SUMMARY);
		expect(sent.map((each) => `${each.method} ${each.url}`)).toEqual([
			'POST http://lab.test/api/v1/lab/experiments/e1/restore',
		]);
	});

	it('cannot be steered to another Lab route by the id', async () => {
		const { sent, event: sly } = event(true, {}, '../vectors');

		await one.GET(sly as never);

		expect(sent.map((each) => each.url)).toEqual([
			'http://lab.test/api/v1/lab/experiments/..%2Fvectors',
		]);
	});

	it('is not found on a deployment without Lab, and Lab is never asked', async () => {
		const { sent, event: absent } = event(false, { method: 'DELETE' });

		await expect(one.DELETE(absent as never)).rejects.toMatchObject({ status: 404 });
		expect(sent).toEqual([]);
	});
});
