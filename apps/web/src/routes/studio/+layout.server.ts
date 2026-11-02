import { requireStudio } from '#lib/server/studio.ts';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ parent }) => {
	requireStudio((await parent()).capabilities);
};
