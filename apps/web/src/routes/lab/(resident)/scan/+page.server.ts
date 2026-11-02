import { error } from '@sveltejs/kit';
import { residentStatus } from '#lib/server/lab.ts';
import type { PageServerLoad } from './$types';

/**
 * Which model Lab has resident. The libraries to scan are the frame's.
 *
 * Refused without Chat as well as without Lab: the passages are read
 * through Chat, so a deployment without it has a Scan tab that could
 * never scan anything.
 */
export const load: PageServerLoad = async ({ request, fetch, parent }) => {
	const { capabilities } = await parent();
	if (!capabilities.chat_available) {
		error(404, 'Scanning reads libraries through Chat, which this deployment does not run.');
	}
	return residentStatus(request, fetch);
};
