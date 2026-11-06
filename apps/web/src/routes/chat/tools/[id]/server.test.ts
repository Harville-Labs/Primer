import type { RequestEvent } from '@sveltejs/kit';
import { describe, expect, it, vi } from 'vitest';

vi.mock('$app/env/private', async () =>
	(await import('#lib/server/private-env.testing.ts')).privateEnv({
		PRIMER_CHAT_URL: 'http://chat.test',
	}),
);

const { POST } = await import('./+server');

function event(id: string, body: unknown, answer: () => Promise<Response>) {
	const sent: { url: string; method: string | undefined; user: string | null }[] = [];
	const fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
		sent.push({
			url: String(input),
			method: init?.method,
			user: new Headers(init?.headers).get('x-forwarded-user'),
		});
		return answer();
	}) as typeof globalThis.fetch;
	const request = new Request(`http://primer.test/chat/tools/${id}`, {
		method: 'POST',
		headers: { 'x-forwarded-user': 'ada' },
		body: JSON.stringify(body),
	});
	return { sent, event: { request, fetch, params: { id } } as unknown as RequestEvent };
}

describe('Deciding a tool call through Chat', () => {
	it('approves the call as the user who asked', async () => {
		const { sent, event: approved } = event('c1', { decision: 'approve' }, async () =>
			Response.json({ id: 'c1', phase: 'approved' }),
		);

		const response = await POST(approved as never);

		expect(response.status).toBe(200);
		expect(sent).toEqual([
			{ url: 'http://chat.test/api/v1/tool-requests/c1/approve', method: 'POST', user: 'ada' },
		]);
	});

	it('refuses anything but approve or deny without calling Chat', async () => {
		// The decision is a path segment on Chat. Anything else could name a
		// different route there.
		const { sent, event: odd } = event('c1', { decision: '../../admin' }, async () =>
			Response.json({}),
		);

		const response = await POST(odd as never);

		expect(response.status).toBe(400);
		expect(sent).toEqual([]);
	});

	it('encodes the call id so it cannot become another path', async () => {
		const { sent, event: sneaky } = event('a/../b', { decision: 'deny' }, async () =>
			Response.json({}),
		);

		await POST(sneaky as never);

		expect(sent[0]?.url).toBe('http://chat.test/api/v1/tool-requests/a%2F..%2Fb/deny');
	});

	it("passes Chat's refusal of a call already decided through whole", async () => {
		const { event: late } = event('c1', { decision: 'approve' }, async () =>
			Response.json(
				{ code: 'TOOL_ALREADY_DECIDED', title: 'Already decided', status: 409, detail: 'x' },
				{ status: 409 },
			),
		);

		const response = await POST(late as never);

		expect(response.status).toBe(409);
		expect(await response.json()).toMatchObject({ title: 'Already decided' });
	});
});
