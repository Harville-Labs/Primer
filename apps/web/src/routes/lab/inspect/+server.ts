import type { InspectRequest } from '#lib/api/types.ts';
import { relay } from '#lib/server/lab.ts';
import type { RequestHandler } from './$types';

// The body is passed on as it came; Lab validates it, and its refusal names
// the field, which a second validation here would only paraphrase.
export const POST: RequestHandler = async (event) => {
	const body = (await event.request.json()) as InspectRequest;
	return relay(event, (lab) => lab.inspect(body));
};
