import { describe, expect, it } from 'vitest';
import { ApiError } from './client';
import { LabApi } from './lab-client';

describe('Lab not answering', () => {
	it('reaches the page as Lab being unreachable, not as a generic failure', async () => {
		const lab = new LabApi({
			fetch: () => Promise.reject(new TypeError('fetch failed')),
		});

		const failure = await lab.status().catch((error: unknown) => error);

		expect(failure).toBeInstanceOf(ApiError);
		const problem = failure as ApiError;
		expect(problem.code).toBe('lab_unreachable');
		expect(problem.status).toBe(503);
		expect(problem.title).toBe('Lab is unreachable');
		// Says whether to try again, which only this side knows.
		expect(problem.message).toMatch(/try again in a minute/);
	});

	it('passes Lab’s own refusal through rather than renaming it', async () => {
		const lab = new LabApi({
			fetch: async () =>
				Response.json(
					{
						code: 'quota_exceeded',
						title: 'Lab is busy',
						status: 429,
						detail: '4 runs are already waiting. Try again shortly.',
						request_id: 'r1',
					},
					{ status: 429 },
				),
		});

		const failure = (await lab
			.inspect({ prompt: 'hi', chat: true, neurons: 8, features: 8 })
			.catch((error: unknown) => error)) as ApiError;

		expect(failure.code).toBe('quota_exceeded');
		expect(failure.title).toBe('Lab is busy');
		expect(failure.status).toBe(429);
	});
});
