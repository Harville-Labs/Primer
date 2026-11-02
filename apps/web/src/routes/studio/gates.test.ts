import { describe, expect, it, vi } from 'vitest';
import type { DeploymentCapabilities } from '#lib/api/types.ts';

vi.mock('$app/env/private', () => ({ PRIMER_CONTROL_URL: 'http://control.test' }));

const { load: layout } = await import('./+layout.server');
const { load: index } = await import('./+page.server');
const { POST: generate } = await import('./generations/+server');

/**
 * Studio on a deployment that has it, and nowhere else.
 *
 * The sidebar hides the link; these are what make a typed URL a 404 rather
 * than a page that loads and then fails.
 */

function capabilities(studio: boolean): DeploymentCapabilities {
	return {
		auth_enabled: false,
		ingestion_available: true,
		chat_available: true,
		tools_available: false,
		lab_available: false,
		studio_available: studio,
		is_admin: false,
		max_upload_bytes: 1024,
		supported_extensions: ['.pdf'],
	};
}

function from(studio: boolean) {
	return { parent: async () => ({ capabilities: capabilities(studio) }) } as never;
}

/** A server-side fetch where Control answers capabilities, recording every URL asked for. */
function event(studio: boolean) {
	const asked: string[] = [];
	const fetch = (async (input: RequestInfo | URL) => {
		asked.push(String(input));
		return Response.json(capabilities(studio));
	}) as typeof globalThis.fetch;
	const request = new Request('http://primer.test/studio/generations', {
		method: 'POST',
		body: JSON.stringify({ prompt: 'a fox' }),
	});
	return { asked, event: { request, fetch } as never };
}

describe('the Studio section', () => {
	it('is not found on a deployment without Studio', async () => {
		await expect(layout(from(false))).rejects.toMatchObject({ status: 404 });
	});

	it('opens on Generate where Studio is on', async () => {
		await expect(layout(from(true))).resolves.toBeUndefined();
		await expect(index(from(true))).rejects.toMatchObject({
			status: 307,
			location: '/studio/generate',
		});
	});

	it('refuses a generation with a 404 without Studio, and never asks Control to make one', async () => {
		const { asked, event: without } = event(false);

		await expect(generate(without)).rejects.toMatchObject({ status: 404 });
		expect(asked).toEqual(['http://control.test/api/v1/capabilities']);
	});
});
