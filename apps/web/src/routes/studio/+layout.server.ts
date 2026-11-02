import { owns } from '#lib/libraries.ts';
import { requireStudio } from '#lib/server/studio.ts';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ parent }) => {
	const { capabilities, libraries, principal } = await parent();
	requireStudio(capabilities);
	return {
		// Where an image may be added: none at all when nothing would read it,
		// and only libraries the caller may change, since Control refuses the rest.
		libraryTargets: capabilities.studio_library_available
			? libraries.filter((library) => owns(principal, library))
			: [],
	};
};
