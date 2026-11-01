import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/** The first screen there is: Neurons needs Lab, Anomalies does not. */
export const load: PageServerLoad = async ({ parent }) => {
	const { capabilities } = await parent();
	redirect(307, capabilities.lab_available ? '/lab/neurons' : '/lab/anomalies');
};
