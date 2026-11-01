import type { RequestEvent } from '@sveltejs/kit';
import { describe, expect, it, vi } from 'vitest';
import type { DeploymentCapabilities } from '#lib/api/types.ts';

vi.mock('$app/env/private', () => ({
	PRIMER_CONTROL_URL: 'http://control.test',
	PRIMER_LAB_URL: 'http://lab.test',
}));

const { relay } = await import('./lab');

function capabilities(lab: boolean): DeploymentCapabilities {
	return {
		auth_enabled: true,
		ingestion_available: true,
		chat_available: true,
		tools_available: false,
		lab_available: lab,
		is_admin: false,
		max_upload_bytes: 1024,
		supported_extensions: ['.pdf'],
	};
}

/**
 * A server-side fetch where Control answers capabilities and Lab does
 * whatever `lab` says, recording every URL asked for.
 */
function event(lab: boolean, answerLab: () => Promise<Response>) {
	const asked: string[] = [];
	const fetch = (async (input: RequestInfo | URL) => {
		const url = String(input);
		asked.push(url);
		if (url.startsWith('http://control.test')) return Response.json(capabilities(lab));
		return answerLab();
	}) as typeof globalThis.fetch;
	const request = new Request('http://primer.test/lab/status', {
		headers: { 'x-forwarded-user': 'ada' },
	});
	return { asked, event: { request, fetch } as unknown as RequestEvent };
}

describe('Lab relay', () => {
	it('refuses with a 404 on a deployment without Lab, and never calls Lab', async () => {
		const { asked, event: without } = event(false, async () => Response.json({}));

		await expect(relay(without, (lab) => lab.status())).rejects.toMatchObject({ status: 404 });
		expect(asked.some((url) => url.startsWith('http://control.test'))).toBe(true);
		expect(asked.some((url) => url.startsWith('http://lab.test'))).toBe(false);
	});

	it('answers an unreachable Lab with a problem that names Lab, not a generic error', async () => {
		const { event: down } = event(true, async () => {
			throw new TypeError('fetch failed');
		});

		const response = await relay(down, (lab) => lab.status());

		expect(response.status).toBe(503);
		const problem = await response.json();
		expect(problem.code).toBe('lab_unreachable');
		expect(problem.title).toBe('Lab is unreachable');
		expect(problem.detail).toContain('try again in a minute');
	});

	it("passes Lab's own refusal through with its title", async () => {
		const { event: busy } = event(true, async () =>
			Response.json(
				{
					code: 'QUOTA_EXCEEDED',
					title: 'Lab is busy',
					status: 429,
					detail: 'Too many runs are queued.',
				},
				{ status: 429 },
			),
		);

		const response = await relay(busy, (lab) => lab.status());

		expect(response.status).toBe(429);
		expect(await response.json()).toMatchObject({
			title: 'Lab is busy',
			detail: 'Too many runs are queued.',
		});
	});
});
