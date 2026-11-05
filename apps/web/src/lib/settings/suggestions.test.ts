import { describe, expect, it } from 'vitest';
import type { DeploymentCapabilities, DeploymentStatus, ProviderSummary } from '#lib/api/types.ts';
import { type DeploymentReport, DOCS_URL, deploymentSuggestions } from './suggestions';

function capabilities(overrides: Partial<DeploymentCapabilities> = {}): DeploymentCapabilities {
	return {
		auth_enabled: true,
		ingestion_available: true,
		chat_available: true,
		tools_available: true,
		lab_available: true,
		studio_available: true,
		studio_library_available: true,
		max_upload_bytes: 1_000_000,
		supported_extensions: ['.pdf'],
		is_admin: true,
		...overrides,
	};
}

function status(overrides: Partial<DeploymentStatus> = {}): DeploymentStatus {
	return {
		auth_mode: 'oidc',
		admin_group: 'primer-admins',
		dependencies: [],
		vision_configured: true,
		...overrides,
	};
}

function provider(overrides: Partial<ProviderSummary> = {}): ProviderSummary {
	return {
		id: 'p1',
		name: 'local',
		base_url: 'http://vllm:8000/v1',
		api_key_set: false,
		enabled: true,
		serves_chat: true,
		source: 'configured',
		supports_logprobs: false,
		supports_prompt_logprobs: false,
		...overrides,
	};
}

/** Everything configured: the baseline each test takes one thing away from. */
function complete(overrides: Partial<DeploymentReport> = {}): DeploymentReport {
	return {
		capabilities: capabilities(),
		status: status(),
		providers: [provider()],
		...overrides,
	};
}

function ids(report: DeploymentReport): string[] {
	return deploymentSuggestions(report).map((suggestion) => suggestion.id);
}

describe('Suggesting what a deployment is missing', () => {
	it('suggests nothing for a deployment with every part configured', () => {
		expect(deploymentSuggestions(complete())).toEqual([]);
	});

	it('suggests a vision model, by its settings, when ingestion has none', () => {
		const [vision] = deploymentSuggestions(
			complete({ status: status({ vision_configured: false }) }),
		);

		expect(vision.id).toBe('vision');
		expect(vision.gain).toContain('indexed by their text only');
		expect(vision.compose).toEqual(['PRIMER_VISION_BASE_URL', 'PRIMER_VISION_MODEL']);
		expect(vision.chart).toEqual(['inference.vision.baseUrl', 'inference.vision.model']);
		expect(vision.docs).toBe(`${DOCS_URL}operations/models/#what-a-figure-shows`);
	});

	it('suggests nothing about vision when Control could not be asked', () => {
		// The page already says the status is unknown. Suggesting a vision
		// model on top of that would send an operator to fix what may be fine.
		expect(ids(complete({ status: null }))).not.toContain('vision');
	});

	it('warns when no enabled provider serves chat', () => {
		const providers = [provider({ enabled: false }), provider({ id: 'p2', serves_chat: false })];

		const [chat] = deploymentSuggestions(complete({ providers }));

		expect(chat.id).toBe('chat');
		expect(chat.severity).toBe('warning');
	});

	it('warns when there are no providers at all', () => {
		expect(ids(complete({ providers: [] }))).toEqual(['chat']);
	});

	it('suggests nothing about chat when Chat could not be asked', () => {
		expect(ids(complete({ providers: null }))).not.toContain('chat');
	});

	it('suggests tools with no Compose settings, since Compose cannot run them', () => {
		const [tools] = deploymentSuggestions(
			complete({ capabilities: capabilities({ tools_available: false }) }),
		);

		expect(tools.id).toBe('tools');
		expect(tools.compose).toBeNull();
		expect(tools.chart).toContain('tools.enabled');
	});

	it('suggests Studio and Lab when each is off', () => {
		const report = complete({
			capabilities: capabilities({ studio_available: false, lab_available: false }),
		});

		expect(ids(report)).toEqual(['studio', 'lab']);
	});

	it('puts a missing chat model ahead of everything optional', () => {
		const report: DeploymentReport = {
			capabilities: capabilities({
				tools_available: false,
				studio_available: false,
				lab_available: false,
			}),
			status: status({ vision_configured: false }),
			providers: [],
		};

		expect(ids(report)).toEqual(['chat', 'vision', 'tools', 'studio', 'lab']);
	});

	it('links every suggestion to the published documentation', () => {
		const report: DeploymentReport = {
			capabilities: capabilities({
				tools_available: false,
				studio_available: false,
				lab_available: false,
			}),
			status: status({ vision_configured: false }),
			providers: [],
		};
		const suggestions = deploymentSuggestions(report);

		expect(suggestions).toHaveLength(5);
		for (const suggestion of suggestions) expect(suggestion.docs.startsWith(DOCS_URL)).toBe(true);
	});
});
