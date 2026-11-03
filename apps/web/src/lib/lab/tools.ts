import type { DeploymentCapabilities } from '#lib/api/types.ts';

/**
 * Lab's tools, and which of them this deployment can run.
 *
 * One list, read by the sidebar and by the explainers, so a tool offered in
 * one place and missing from the other is not something that can happen by
 * forgetting to edit the second.
 */

export type LabTool = 'neurons' | 'anomalies' | 'scan';

export interface LabToolEntry {
	tool: LabTool;
	label: string;
	href: string;
	/** Where its explainer is. */
	about: string;
}

export const LAB_TOOLS: readonly LabToolEntry[] = [
	{ tool: 'neurons', label: 'Neurons', href: '/lab/neurons', about: '/lab/about/neurons' },
	{ tool: 'anomalies', label: 'Anomalies', href: '/lab/anomalies', about: '/lab/about/anomalies' },
	{ tool: 'scan', label: 'Scan', href: '/lab/scan', about: '/lab/about/scan' },
];

/**
 * Whether a tool can run here. Neurons reads Lab's resident model;
 * Anomalies can score a chat endpoint too, so it is offered wherever Lab
 * is; Scan reads a library through Chat and scores it with Lab, so it
 * needs both.
 */
export function runs(tool: LabTool, capabilities: DeploymentCapabilities): boolean {
	switch (tool) {
		case 'neurons':
			return capabilities.lab_available;
		case 'anomalies':
			return true;
		case 'scan':
			return capabilities.lab_available && capabilities.chat_available;
	}
}

/** The tools this deployment can run, in the order the sidebar lists them. */
export function labTools(capabilities: DeploymentCapabilities): LabToolEntry[] {
	return LAB_TOOLS.filter((entry) => runs(entry.tool, capabilities));
}
