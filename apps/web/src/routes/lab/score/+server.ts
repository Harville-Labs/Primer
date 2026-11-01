import type { LabScoreRequest } from '#lib/api/types.ts';
import { relay } from '#lib/server/lab.ts';
import type { RequestHandler } from './$types';

// Passed on as it came, as the other Lab routes are: Lab's refusal names
// the field, which a second validation here would only paraphrase.
export const POST: RequestHandler = async (event) => {
	const body = (await event.request.json()) as LabScoreRequest;
	return relay(event, (lab) => lab.score(body));
};
