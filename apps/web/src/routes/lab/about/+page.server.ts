import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/** The explainers start where the tools do, with Neurons. */
export const load: PageServerLoad = () => {
	redirect(307, '/lab/about/neurons');
};
