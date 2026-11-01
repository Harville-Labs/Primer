import type { ExperimentRename } from '#lib/api/types.ts';
import { relay } from '#lib/server/lab.ts';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = (event) =>
	relay(event, (lab) => lab.experiment(event.params.id));

export const PATCH: RequestHandler = async (event) => {
	const body = (await event.request.json()) as ExperimentRename;
	return relay(event, (lab) => lab.renameExperiment(event.params.id, body));
};

export const DELETE: RequestHandler = (event) =>
	relay(event, (lab) => lab.deleteExperiment(event.params.id));
