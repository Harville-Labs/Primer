import { requireLab } from '#lib/server/lab.ts';
import type { LayoutServerLoad } from './$types';

/** Lab exists on this deployment, or nothing under `/lab` does. */
export const load: LayoutServerLoad = async ({ parent }) => {
	requireLab((await parent()).capabilities);
};
