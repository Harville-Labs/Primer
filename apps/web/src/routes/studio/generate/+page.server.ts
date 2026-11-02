import { apiFor } from '#lib/server/api.ts';
import { chatFor } from '#lib/server/chat.ts';
import { attempt } from '#lib/server/problem.ts';
import { offersFor } from '#lib/studio/offer.ts';
import type { PageServerLoad } from './$types';

/**
 * What can make an image, and what this person has made.
 *
 * Each fails on its own: an image endpoint that is down still leaves the
 * history to look at, and the reverse. The providers are reduced to offers
 * here, so a template's workflow never reaches the browser.
 */
export const load: PageServerLoad = async ({ request, fetch }) => {
	const [providers, history] = await Promise.all([
		attempt(() => chatFor(request, fetch).imageProviders()),
		attempt(() => apiFor(request, fetch).studioImages()),
	]);
	return {
		offers: 'value' in providers ? offersFor(providers.value, 'generate') : [],
		providersProblem: 'problem' in providers ? providers.problem : null,
		history: 'value' in history ? history.value : [],
		historyProblem: 'problem' in history ? history.problem : null,
	};
};
