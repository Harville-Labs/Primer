import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ parent }) => {
	// Awaited so the layout's 404 wins over the redirect on a deployment without Studio.
	await parent();
	redirect(307, '/studio/generate');
};
