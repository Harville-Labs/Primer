import { studioRelay } from '#lib/server/studio.ts';
import type { RequestHandler } from './$types';

/** A page of the caller's history, older than `before` when given. */
export const GET: RequestHandler = (event) =>
	studioRelay(event, (control) =>
		control.studioImages(event.url.searchParams.get('before') ?? undefined),
	);
