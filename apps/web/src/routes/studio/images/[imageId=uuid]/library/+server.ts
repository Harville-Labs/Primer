import type { StudioToLibrary } from '#lib/api/types.ts';
import { studioRelay } from '#lib/server/studio.ts';
import type { RequestHandler } from './$types';

/**
 * Add a copy of an image to a library. Control validates the id and
 * decides whether the caller may change that library, as it does for an
 * upload; this only carries the request there.
 */
export const POST: RequestHandler = async (event) => {
	const { library_id } = (await event.request.json()) as StudioToLibrary;
	return studioRelay(event, (control) =>
		control.addStudioImageToLibrary(event.params.imageId, library_id),
	);
};
