import { fail } from '@sveltejs/kit';
import { ApiError } from '#lib/api/client.ts';
import { apiFor } from '#lib/server/api.ts';
import type { Actions } from './$types';

export const actions: Actions = {
	// Form actions rather than client-side fetch, so creating and deleting
	// work before JavaScript loads and keep working if it fails to.
	create: async ({ request, fetch }) => {
		const form = await request.formData();
		const name = String(form.get('name') ?? '').trim();
		if (!name) {
			return fail(400, { name, error: 'A library needs a name.' });
		}
		try {
			await apiFor(request, fetch).createLibrary(name);
		} catch (error) {
			if (error instanceof ApiError) return fail(error.status, { name, error: error.message });
			throw error;
		}
		return { created: true };
	},

	duplicate: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = String(form.get('id') ?? '');
		try {
			await apiFor(request, fetch).duplicateLibrary(id);
		} catch (error) {
			if (error instanceof ApiError) return fail(error.status, { error: error.message });
			throw error;
		}
		return { duplicated: true };
	},

	delete: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = String(form.get('id') ?? '');
		try {
			await apiFor(request, fetch).deleteLibrary(id);
		} catch (error) {
			if (error instanceof ApiError) return fail(error.status, { error: error.message });
			throw error;
		}
		return { deleted: true };
	},
};
