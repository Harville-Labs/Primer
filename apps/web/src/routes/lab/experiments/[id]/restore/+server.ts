import { relay } from '#lib/server/lab.ts';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = (event) =>
	relay(event, (lab) => lab.restoreExperiment(event.params.id));
