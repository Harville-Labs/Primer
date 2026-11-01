import { json } from '@sveltejs/kit';
import { ApiError } from '#lib/api/client.ts';
import type { ProblemDetail } from '#lib/api/types.ts';

/**
 * Answer the browser with what a service answered, its refusals included.
 *
 * The problem document is passed through whole rather than flattened into
 * SvelteKit's `{ message }`: its title says which of several refusals this
 * was (busy, too long, not declared), and the browser shows the title and
 * the detail separately. Anything that is not a service's answer is a bug
 * here, and is left to fail as one.
 */
export async function answeredWithProblems(respond: () => Promise<Response>): Promise<Response> {
	try {
		return await respond();
	} catch (failure) {
		if (!(failure instanceof ApiError)) throw failure;
		const problem: ProblemDetail = {
			code: failure.code,
			title: failure.title,
			status: failure.status,
			detail: failure.message,
			request_id: failure.requestId,
		};
		return json(problem, { status: failure.status });
	}
}
