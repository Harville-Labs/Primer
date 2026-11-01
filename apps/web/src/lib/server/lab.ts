import { error, json, type RequestEvent } from '@sveltejs/kit';
import { ApiError } from '#lib/api/client.ts';
import { LabApi } from '#lib/api/lab-client.ts';
import type { DeploymentCapabilities, ProblemDetail } from '#lib/api/types.ts';
import { apiFor } from '#lib/server/api.ts';
import { forwardingIdentity } from '#lib/server/forward.ts';
import { PRIMER_LAB_URL } from '$app/env/private';

/** The server's connection to Lab, as the user the request is from. */
export function labFor(request: Request, fetch: typeof globalThis.fetch): LabApi {
	return new LabApi({ baseUrl: PRIMER_LAB_URL, fetch: forwardingIdentity(request, fetch) });
}

/**
 * Refuse every Lab route on a deployment that has none.
 *
 * One rule for pages and endpoints alike, so a hidden link cannot be the
 * only thing standing between a user and a service the operator did not
 * enable.
 */
export function requireLab(capabilities: DeploymentCapabilities): void {
	if (!capabilities.lab_available) error(404, 'This deployment has no Lab.');
}

/**
 * Answer the browser with what Lab answered, problems included.
 *
 * Lab's problem document is passed through whole rather than flattened into
 * SvelteKit's `{ message }`: its title says which of several refusals this
 * was (busy, too long, model changed), and the browser shows the title and
 * the detail separately.
 */
export async function relay(
	event: RequestEvent,
	call: (lab: LabApi) => Promise<unknown>,
): Promise<Response> {
	requireLab(await apiFor(event.request, event.fetch).capabilities());
	try {
		return json(await call(labFor(event.request, event.fetch)));
	} catch (failure) {
		if (!(failure instanceof ApiError)) throw failure;
		const problem: ProblemDetail = {
			code: failure.code,
			title: failure.title,
			status: failure.status,
			detail: failure.message,
			request_id: failure.requestId,
		};
		return json(problem, { status: failure.status });
	}
}
