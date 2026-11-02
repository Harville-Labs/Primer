import { studioRelay } from '#lib/server/studio.ts';
import type { RequestHandler } from './$types';

export const DELETE: RequestHandler = (event) =>
	studioRelay(event, (control) => control.deleteStudioImage(event.params.imageId));
