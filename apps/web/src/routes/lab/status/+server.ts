import { relay } from '#lib/server/lab.ts';
import type { RequestHandler } from './$types';

/** Polled while a run waits, for how many runs are ahead of it. */
export const GET: RequestHandler = (event) => relay(event, (lab) => lab.status());
