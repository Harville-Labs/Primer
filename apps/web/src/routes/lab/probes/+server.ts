import type { ProbeRequest } from '#lib/api/types.ts';
import { relay } from '#lib/server/lab.ts';
import type { RequestHandler } from './$types';

/** The caller's own probes; Lab decides whose they are. */
export const GET: RequestHandler = (event) => relay(event, (lab) => lab.probes());

export const POST: RequestHandler = async (event) => {
	const body = (await event.request.json()) as ProbeRequest;
	return relay(event, (lab) => lab.trainProbe(body));
};
