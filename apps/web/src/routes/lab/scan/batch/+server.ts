import type { LabScanRequest } from '#lib/api/types.ts';
import { relay } from '#lib/server/lab.ts';
import type { RequestHandler } from './$types';

/** One batch of a scan. Lab is given the text; which library it came from stays here. */
export const POST: RequestHandler = async (event) => {
	const body = (await event.request.json()) as LabScanRequest;
	return relay(event, (lab) => lab.scan(body));
};
