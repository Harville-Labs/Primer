import { describe, expect, it, vi } from 'vitest';

vi.mock('$app/env/private', async () =>
	(await import('#lib/server/private-env.testing.ts')).privateEnv(
		{ PRIMER_SUBJECT_HEADER: 'x-auth-subject' },
		'web-secret',
	),
);

const { forwardingIdentity } = await import('./forward');

function sentHeaders(request: Request): Promise<Headers> {
	return new Promise((resolve) => {
		const fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
			resolve(new Headers(init?.headers));
			return new Response();
		}) as typeof globalThis.fetch;
		void forwardingIdentity(request, fetch)('http://control.test/x', {
			headers: { 'content-type': 'application/json' },
		});
	});
}

describe('forwarding identity to a service', () => {
	it('presents the web credential, so the service believes the identity beside it', async () => {
		// Services refuse forwarded identity without it: anything else in the
		// cluster could otherwise send the same headers and be anyone.
		const headers = await sentHeaders(new Request('http://primer.test/'));

		expect(headers.get('x-primer-web-token')).toBe('web-secret');
		expect(headers.get('content-type')).toBe('application/json');
	});

	it('copies the subject under the header the deployment configured', async () => {
		const request = new Request('http://primer.test/', {
			headers: { 'x-auth-subject': 'ada', 'x-forwarded-user': 'mallory', cookie: 'session=1' },
		});

		const headers = await sentHeaders(request);

		expect(headers.get('x-auth-subject')).toBe('ada');
		expect(headers.get('x-forwarded-user')).toBeNull();
		expect(headers.get('cookie')).toBeNull();
	});
});
