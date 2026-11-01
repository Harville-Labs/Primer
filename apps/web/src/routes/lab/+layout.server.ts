import { error } from '@sveltejs/kit';
import { canScore } from '#lib/lab/anomalies.ts';
import type { LayoutServerLoad } from './$types';

/**
 * Something to evaluate exists on this deployment, or nothing under `/lab`
 * does. The screens that need Lab itself say so again in `(resident)`.
 */
export const load: LayoutServerLoad = async ({ parent }) => {
	if (!canScore((await parent()).capabilities)) error(404, 'This deployment has no Lab.');
};
