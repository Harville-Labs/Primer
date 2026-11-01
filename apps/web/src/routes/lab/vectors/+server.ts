import type { SteeringVectorRequest } from '#lib/api/types.ts';
import { relay } from '#lib/server/lab.ts';
import type { RequestHandler } from './$types';

/** The caller's own steering vectors; Lab decides whose they are. */
export const GET: RequestHandler = (event) => relay(event, (lab) => lab.vectors());

export const POST: RequestHandler = async (event) => {
	const body = (await event.request.json()) as SteeringVectorRequest;
	return relay(event, (lab) => lab.createVector(body));
};
