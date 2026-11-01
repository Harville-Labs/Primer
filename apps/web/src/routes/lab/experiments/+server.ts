import type { ExperimentCreate } from '#lib/api/types.ts';
import { relay } from '#lib/server/lab.ts';
import type { RequestHandler } from './$types';

/** The caller's saved experiments; Lab decides whose they are. */
export const GET: RequestHandler = (event) => relay(event, (lab) => lab.experiments());

export const POST: RequestHandler = async (event) => {
	const body = (await event.request.json()) as ExperimentCreate;
	return relay(event, (lab) => lab.saveExperiment(body));
};
