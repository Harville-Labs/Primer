import type { RequestEvent } from '@sveltejs/kit';
import { describe, expect, it, vi } from 'vitest';

vi.mock('$app/env/private', async () =>
	(await import('#lib/server/private-env.testing.ts')).privateEnv({
		PRIMER_CONTROL_URL: 'http://control.test',
		PRIMER_CHAT_URL: 'http://chat.test',
	}),
);

const { actions } = await import('./+page.server');

/** A form post to an action, and every request it sent on to Chat. */
function post(fields: Record<string, string>) {
	const sent: { url: string; method: string; body: unknown }[] = [];
	const fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
		sent.push({
			url: String(input),
			method: init?.method ?? 'GET',
			body: init?.body ? JSON.parse(String(init.body)) : null,
		});
		return Response.json({});
	}) as typeof globalThis.fetch;
	const request = new Request('http://primer.test/settings', {
		method: 'POST',
		headers: { 'x-forwarded-user': 'admin' },
		body: new URLSearchParams(fields),
	});
	return { sent, event: { request, fetch } as unknown as RequestEvent };
}

describe('Declaring what a provider returns', () => {
	it('sends the one flag that was changed, and nothing else', async () => {
		const { sent, event } = post({ id: 'p1', flag: 'supports_prompt_logprobs', value: 'true' });

		await actions.declare(event as never);

		expect(sent).toHaveLength(1);
		expect(sent[0].url).toBe('http://chat.test/api/v1/admin/providers/p1');
		expect(sent[0].body).toEqual({ supports_prompt_logprobs: true });
	});

	it('refuses a field that is not a declaration, rather than passing it to Chat', async () => {
		// The field name comes from the browser; without the allow-list this
		// would rename or repoint a provider through a checkbox's action.
		const { sent, event } = post({ id: 'p1', flag: 'base_url', value: 'http://elsewhere' });

		const result = await actions.declare(event as never);

		expect(result).toMatchObject({ status: 400 });
		expect(sent).toEqual([]);
	});

	it('adds a provider with what its form declared', async () => {
		const { sent, event } = post({
			name: 'Workstation',
			base_url: 'http://gpu:8000/v1',
			supports_logprobs: 'true',
			supports_prompt_logprobs: 'false',
		});

		await actions.add(event as never);

		expect(sent).toHaveLength(1);
		expect(sent[0].body).toMatchObject({
			supports_logprobs: true,
			supports_prompt_logprobs: false,
		});
	});
});

describe('Declaring what a provider does with images', () => {
	const declared = {
		image_models: 'flux',
		image_generate: 'true',
		image_sizes: '1024x1024',
	};

	it('adds an image-only provider with its declaration', async () => {
		const { sent, event } = post({
			name: 'Omni',
			base_url: 'http://gpu:8091/v1',
			serves_chat: 'false',
			...declared,
		});

		await actions.add(event as never);

		expect(sent[0].body).toMatchObject({
			serves_chat: false,
			images: { models: ['flux'], operations: ['generate'], sizes: ['1024x1024'] },
		});
	});

	it('refuses a provider that would serve nothing, before asking Chat', async () => {
		const { sent, event } = post({ name: 'Omni', base_url: 'http://gpu/v1', serves_chat: 'false' });

		const result = await actions.add(event as never);

		expect(result).toMatchObject({ status: 400 });
		expect(sent).toEqual([]);
	});

	it('changes only the declaration of an existing provider', async () => {
		const { sent, event } = post({ id: 'p1', ...declared });

		await actions.images(event as never);

		expect(sent[0].method).toBe('PATCH');
		expect(Object.keys(sent[0].body as object)).toEqual(['images']);
	});

	it('withdraws the declaration only when asked to', async () => {
		// An emptied models field is a mistake far more often than a decision.
		const emptied = post({ id: 'p1', image_models: '' });
		expect(await actions.images(emptied.event as never)).toMatchObject({ status: 400 });
		expect(emptied.sent).toEqual([]);

		const withdrawn = post({ id: 'p1', withdraw: 'true' });
		await actions.images(withdrawn.event as never);
		expect(withdrawn.sent[0].body).toEqual({ images: null });
	});
});
