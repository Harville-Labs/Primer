import { describe, expect, it } from 'vitest';
import type { DeploymentCapabilities } from '#lib/api/types.ts';
import { load as lab } from './+layout.server';
import { load as index } from './+page.server';
import { load as resident } from './(resident)/+layout.server';
import { load as scan } from './(resident)/scan/+page.server';

/**
 * Which Lab screens a deployment reaches.
 *
 * Anomalies can score an operator's chat endpoint, so a deployment with
 * Chat and no Lab reaches it; Neurons reads Lab's model and does not.
 * Scan reads libraries through Chat and scores them with Lab, so it needs
 * both.
 */

function capabilities(labAvailable: boolean, chat: boolean): DeploymentCapabilities {
	return {
		auth_enabled: false,
		ingestion_available: true,
		chat_available: chat,
		tools_available: false,
		lab_available: labAvailable,
		is_admin: false,
		max_upload_bytes: 1024,
		supported_extensions: ['.pdf'],
	};
}

function from(labAvailable: boolean, chat: boolean) {
	return { parent: async () => ({ capabilities: capabilities(labAvailable, chat) }) } as never;
}

describe('the Lab section', () => {
	it('opens on a deployment with Chat and no Lab, and only Anomalies is reachable', async () => {
		await expect(lab(from(false, true))).resolves.toBeUndefined();
		await expect(resident(from(false, true))).rejects.toMatchObject({ status: 404 });
		await expect(index(from(false, true))).rejects.toMatchObject({
			status: 307,
			location: '/lab/anomalies',
		});
	});

	it('opens on Neurons where Lab is deployed', async () => {
		await expect(resident(from(true, false))).resolves.toBeUndefined();
		await expect(index(from(true, false))).rejects.toMatchObject({ location: '/lab/neurons' });
	});

	it('does not offer Scan where Lab has no Chat to read libraries through', async () => {
		await expect(scan(from(true, false))).rejects.toMatchObject({ status: 404 });
	});

	it('is not found on a deployment with neither', async () => {
		await expect(lab(from(false, false))).rejects.toMatchObject({ status: 404 });
	});
});
