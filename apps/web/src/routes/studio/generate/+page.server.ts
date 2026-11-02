import { studioTab } from '#lib/server/studio.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = (event) => studioTab(event, ['generate']);
