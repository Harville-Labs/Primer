import { asProblem } from '#lib/api/client.ts';

/** Why the last thing asked of Lab did not happen, in Lab's own words. */
export interface Failure {
	title: string;
	detail: string;
}

export class ProblemFailure extends Error {
	constructor(readonly failure: Failure) {
		super(failure.detail);
	}
}

export async function problemOf(response: Response): Promise<ProblemFailure> {
	let body: unknown = null;
	try {
		body = await response.json();
	} catch {
		// Not JSON: asProblem below still says what the status was.
	}
	const problem = asProblem(body, response.status);
	return new ProblemFailure({
		title: problem.title,
		detail: problem.detail ?? problem.title,
	});
}

export function asFailure(failure: unknown): Failure {
	if (failure instanceof ProblemFailure) return failure.failure;
	// The fetch itself failing means this page's own server did not answer,
	// which is a different thing from Lab not answering, and is said so.
	return {
		title: 'Primer did not answer',
		detail: 'The request did not reach Primer. Check your connection and try again.',
	};
}
