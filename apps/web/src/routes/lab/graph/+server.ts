import type { GraphRequest } from '#lib/api/types.ts';
import { relay } from '#lib/server/lab.ts';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async (event) => {
	const body = (await event.request.json()) as GraphRequest;
	return relay(event, (lab) => lab.graph(body));
};
