import { requireLab } from '#lib/server/lab.ts';
import type { LayoutServerLoad } from './$types';

/**
 * The screens that read Lab's resident model, and only work where Lab is.
 *
 * A group of their own because Anomalies, beside them, also scores an
 * operator's chat endpoint and is reachable without Lab. Gated here rather
 * than page by page, so a new screen in the group cannot forget to be.
 */
export const load: LayoutServerLoad = async ({ parent }) => {
	requireLab((await parent()).capabilities);
};
