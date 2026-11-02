import { studioTab } from '#lib/server/studio.ts';
import type { PageServerLoad } from './$types';

/**
 * As Generate, with the image to start from when another tab sent one.
 *
 * It is looked for in the history already loaded rather than fetched: an
 * image is sent here from a page that showed it, so it is almost always
 * recent, and one that is not is picked again from the history.
 */
export const load: PageServerLoad = async (event) => {
	const tab = await studioTab(event, ['edit', 'inpaint']);
	const wanted = event.url.searchParams.get('source');
	return { ...tab, source: tab.history.find((each) => each.id === wanted) ?? null };
};
