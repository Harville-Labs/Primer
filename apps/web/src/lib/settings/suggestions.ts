import type { DeploymentCapabilities, DeploymentStatus, ProviderSummary } from '#lib/api/types.ts';

/**
 * What this deployment is missing, and what each missing part would add.
 *
 * Read only from what the services already report, so a suggestion is a
 * statement about this deployment rather than a guess. Where a report could
 * not be had - Control or Chat did not answer - the suggestions that depend
 * on it are left out: the settings page already says the report is missing,
 * and suggesting a fix for an unknown would send an operator to change
 * something that may be fine.
 */
export interface Suggestion {
	id: 'chat' | 'vision' | 'tools' | 'studio' | 'lab';
	/** `warning` when the deployment cannot answer a question without it. */
	severity: 'warning' | 'info';
	title: string;
	/** What a reader of this deployment gains, in their terms. */
	gain: string;
	/** Settings to set in Compose's `.env`. Null where Compose cannot run it. */
	compose: string[] | null;
	/** Values to set on the Helm chart. */
	chart: string[];
	docs: string;
}

export interface DeploymentReport {
	capabilities: DeploymentCapabilities;
	status: DeploymentStatus | null;
	providers: ProviderSummary[] | null;
}

/** The published documentation. Absolute, since the app does not serve it. */
export const DOCS_URL = 'https://harville-labs.github.io/Primer/';

function docs(path: string): string {
	return new URL(path, DOCS_URL).toString();
}

/** In order of how much each one matters to someone asking a question. */
export function deploymentSuggestions(report: DeploymentReport): Suggestion[] {
	const { capabilities, status, providers } = report;
	const suggestions: Suggestion[] = [];

	if (providers !== null && !providers.some((p) => p.enabled && p.serves_chat)) {
		suggestions.push({
			id: 'chat',
			severity: 'warning',
			title: 'No chat model is answering',
			gain: 'Without an enabled provider that serves chat, no question can be answered. Add one below, or point the deployment at one.',
			compose: ['PRIMER_CHAT_BASE_URL'],
			chart: ['inference.chat.baseUrl'],
			docs: docs('operations/providers/#chat'),
		});
	}

	if (status !== null && !status.vision_configured) {
		suggestions.push({
			id: 'vision',
			severity: 'info',
			title: 'No vision model configured',
			gain: 'Figures in documents are indexed by their text only. A vision model describes what each figure shows, so a chart or diagram can be found by its content.',
			compose: ['PRIMER_VISION_BASE_URL', 'PRIMER_VISION_MODEL'],
			chart: ['inference.vision.baseUrl', 'inference.vision.model'],
			docs: docs('operations/models/#what-a-figure-shows'),
		});
	}

	if (!capabilities.tools_available) {
		suggestions.push({
			id: 'tools',
			severity: 'info',
			title: 'No tools configured',
			gain: 'Answers come from libraries only. With a web search server, the model can search the web, and each call is approved by the person asking.',
			compose: null,
			chart: ['tools.enabled', 'tools.search.enabled'],
			docs: docs('operations/tools/'),
		});
	}

	if (!capabilities.studio_available) {
		suggestions.push({
			id: 'studio',
			severity: 'info',
			title: 'Studio is off',
			gain: 'Studio makes and edits images through an image server you run, the OpenAI images API or ComfyUI.',
			compose: ['PRIMER_STUDIO_ENABLED', 'PRIMER_IMAGES_BASE_URL'],
			chart: ['studio.enabled', 'inference.images.baseUrl'],
			docs: docs('operations/studio/'),
		});
	}

	if (!capabilities.lab_available) {
		suggestions.push({
			id: 'lab',
			severity: 'info',
			title: 'Lab is off',
			gain: 'Lab is a tab for looking inside a small open model while it runs: which features a prompt lights up, and what the output does when one is turned up or down.',
			compose: ['COMPOSE_PROFILES=lab', 'PRIMER_LAB_SERVICE_URL'],
			chart: ['lab.enabled'],
			docs: docs('operations/lab/'),
		});
	}

	return suggestions;
}
