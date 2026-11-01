import { ApiError } from '#lib/api/client.ts';
import type { LabStatus } from '#lib/api/types.ts';
import { labFor } from '#lib/server/lab.ts';
import type { PageServerLoad } from './$types';

/**
 * Which model Lab has resident, for the card beside the prompt.
 *
 * A Lab that does not answer is not a page that will not load: the screen
 * still renders, says Lab is unreachable in Lab's own words, and the first
 * run tries again. Lab can take minutes to load its model, and an error
 * page for that would hide the explanation behind a retry button.
 */
export const load: PageServerLoad = async ({ request, fetch }) => {
	let status: LabStatus | null = null;
	let statusProblem: { title: string; detail: string } | null = null;
	try {
		status = await labFor(request, fetch).status();
	} catch (failure) {
		if (!(failure instanceof ApiError)) throw failure;
		statusProblem = { title: failure.title, detail: failure.message };
	}
	return { status, statusProblem };
};
