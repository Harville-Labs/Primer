import { error, fail } from '@sveltejs/kit';
import { ApiError } from '#lib/api/client.ts';
import type { DeploymentStatus, ProviderSummary } from '#lib/api/types.ts';
import { apiFor } from '#lib/server/api.ts';
import { chatFor } from '#lib/server/chat.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * How this deployment is wired, for whoever runs it.
 *
 * Both halves are asked for, because they live in different services and an
 * operator needs them together: Control knows what it is connected to, Chat
 * knows which endpoints can answer a question.
 *
 * Neither is smoothed over on failure. This is the page someone opens when
 * something is already wrong, so a service that will not answer is the most
 * useful thing it can report rather than a reason to show nothing.
 */
export const load: PageServerLoad = async ({ request, fetch, parent }) => {
	const { capabilities } = await parent();
	// Control decides this, not the browser. The check is repeated on every
	// route behind it; this only keeps the page from rendering a shell that
	// would fill with permission errors.
	if (!capabilities.is_admin) {
		error(403, 'Changing this deployment is restricted to administrators.');
	}

	const [status, providers] = await Promise.all([
		apiFor(request, fetch)
			.deploymentStatus()
			.catch((): DeploymentStatus | null => null),
		capabilities.chat_available
			? chatFor(request, fetch)
					.providers()
					.catch((): ProviderSummary[] | null => null)
			: null,
	]);

	return { status, providers };
};

export const actions: Actions = {
	// Form actions rather than client-side fetch, so a deployment can be
	// repaired from a browser with no JavaScript - which is the state a
	// half-broken deployment is sometimes in.
	add: async ({ request, fetch }) => {
		const form = await request.formData();
		const name = String(form.get('name') ?? '').trim();
		const baseUrl = String(form.get('base_url') ?? '').trim();
		if (!name || !baseUrl) {
			return fail(400, { error: 'A provider needs a name and a URL.' });
		}
		try {
			await chatFor(request, fetch).addProvider({
				name,
				base_url: baseUrl,
				// Absent rather than empty: an empty string means "remove the
				// key", which is not what leaving the field blank asks for.
				api_key: String(form.get('api_key') ?? '') || null,
				enabled: true,
				supports_logprobs: form.get('supports_logprobs') === 'true',
				supports_prompt_logprobs: form.get('supports_prompt_logprobs') === 'true',
			});
		} catch (cause) {
			if (cause instanceof ApiError) return fail(cause.status, { error: cause.message });
			throw cause;
		}
		return { added: true };
	},

	toggle: async ({ request, fetch }) => {
		const form = await request.formData();
		try {
			await chatFor(request, fetch).updateProvider(String(form.get('id')), {
				enabled: form.get('enabled') === 'true',
			});
		} catch (cause) {
			if (cause instanceof ApiError) return fail(cause.status, { error: cause.message });
			throw cause;
		}
		return { updated: true };
	},

	declare: async ({ request, fetch }) => {
		const form = await request.formData();
		const flag = String(form.get('flag'));
		// Allow-listed: the field name is the browser's to send, and any other
		// would be sent on to Chat as a change to something else.
		if (flag !== 'supports_logprobs' && flag !== 'supports_prompt_logprobs') {
			return fail(400, { error: `${flag} is not something a provider declares.` });
		}
		try {
			await chatFor(request, fetch).updateProvider(String(form.get('id')), {
				[flag]: form.get('value') === 'true',
			});
		} catch (cause) {
			if (cause instanceof ApiError) return fail(cause.status, { error: cause.message });
			throw cause;
		}
		return { updated: true };
	},

	remove: async ({ request, fetch }) => {
		const form = await request.formData();
		try {
			await chatFor(request, fetch).removeProvider(String(form.get('id')));
		} catch (cause) {
			if (cause instanceof ApiError) return fail(cause.status, { error: cause.message });
			throw cause;
		}
		return { removed: true };
	},
};
