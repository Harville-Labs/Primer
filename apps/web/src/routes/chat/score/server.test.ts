import type { RequestEvent } from '@sveltejs/kit';
import { describe, expect, it, vi } from 'vitest';

vi.mock('$app/env/private', () => ({ PRIMER_CHAT_URL: 'http://chat.test' }));

const { POST } = await import('./+server');

function event(answer: () => Promise<Response>) {
	const sent: { url: string; body: unknown }[] = [];
	const fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
		sent.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null });
		return answer();
	}) as typeof globalThis.fetch;
	const request = new Request('http://primer.test/chat/score', {
		method: 'POST',
		headers: { 'x-forwarded-user': 'ada' },
		body: JSON.stringify({ provider_id: 'p1', model: 'm', mode: 'text', text: ' a' }),
	});
	return { sent, event: { request, fetch } as unknown as RequestEvent };
}

describe('Scoring through Chat', () => {
	it('passes the body on to Chat as it came, leading space included', async () => {
		const { sent, event: scored } = event(async () => Response.json({ text: ' a' }));

		const response = await POST(scored as never);

		expect(response.status).toBe(200);
		expect(sent).toEqual([
			{
				url: 'http://chat.test/api/v1/score',
				body: { provider_id: 'p1', model: 'm', mode: 'text', text: ' a' },
			},
		]);
	});

	it("passes Chat's refusal through with its title", async () => {
		const { event: refused } = event(async () =>
			Response.json(
				{
					code: 'LOGPROBS_NOT_DECLARED',
					title: 'Prompt logprobs not declared',
					status: 422,
					detail: 'Set PRIMER_CHAT_SUPPORTS_PROMPT_LOGPROBS.',
				},
				{ status: 422 },
			),
		);

		const response = await POST(refused as never);

		expect(response.status).toBe(422);
		expect(await response.json()).toMatchObject({
			title: 'Prompt logprobs not declared',
			detail: 'Set PRIMER_CHAT_SUPPORTS_PROMPT_LOGPROBS.',
		});
	});

	it('answers an unreachable Chat with a problem that names Chat, not a generic error', async () => {
		const { event: down } = event(async () => {
			throw new TypeError('fetch failed');
		});

		const response = await POST(down as never);

		expect(response.status).toBe(503);
		expect(await response.json()).toMatchObject({
			code: 'chat_unreachable',
			title: 'Chat is unreachable',
		});
	});
});
