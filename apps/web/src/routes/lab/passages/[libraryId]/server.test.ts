import { describe, expect, it, vi } from 'vitest';

vi.mock('$app/env/private', () => ({
	PRIMER_CONTROL_URL: 'http://control.test',
	PRIMER_CHAT_URL: 'http://chat.test',
	PRIMER_LAB_URL: 'http://lab.test',
}));

const { GET } = await import('./+server');

/** Control says whether Lab is deployed; Chat lists, or refuses, the rest. */
function event(labAvailable: boolean, libraryId: string, chat: () => Response) {
	const asked: string[] = [];
	const fetch = (async (input: RequestInfo | URL) => {
		const url = String(input);
		if (url.endsWith('/api/v1/capabilities')) {
			return Response.json({ lab_available: labAvailable });
		}
		asked.push(url);
		return chat();
	}) as typeof globalThis.fetch;
	const request = new Request(`http://primer.test/lab/passages/${libraryId}`);
	return { asked, event: { request, fetch, params: { libraryId } } as never };
}

describe('a library’s passages, for a scan', () => {
	it('are read from Chat as the person, up to the scan’s listing, never from Lab', async () => {
		const { asked, event: listed } = event(true, 'lib', () =>
			Response.json({ passages: [], total: 0 }),
		);

		const response = await GET(listed);

		expect(await response.json()).toEqual({ passages: [], total: 0 });
		expect(asked).toEqual(['http://chat.test/api/v1/libraries/lib/passages?limit=2000']);
	});

	/** The id comes from the browser; unencoded, it could name another route. */
	it('cannot be steered to another of Chat’s routes by the library id', async () => {
		const { asked, event: steered } = event(true, '../conversations', () =>
			Response.json({ passages: [], total: 0 }),
		);

		await GET(steered);

		expect(asked).toEqual([
			'http://chat.test/api/v1/libraries/..%2Fconversations/passages?limit=2000',
		]);
	});

	it('answer with Chat’s refusal for a library the person cannot read', async () => {
		const refusal = {
			code: 'not_found',
			title: 'Library not found',
			status: 404,
			detail: 'No library with that identifier is available to you.',
		};
		const { event: refused } = event(true, 'other', () => Response.json(refusal, { status: 404 }));

		const response = await GET(refused);

		expect(response.status).toBe(404);
		expect(await response.json()).toMatchObject({ code: 'not_found' });
	});

	it('are not found on a deployment without Lab, and Chat is never asked', async () => {
		const { asked, event: absent } = event(false, 'lib', () => Response.json({}));

		await expect(GET(absent)).rejects.toMatchObject({ status: 404 });
		expect(asked).toEqual([]);
	});
});
