import { error, json, type RequestEvent } from '@sveltejs/kit';
import { ApiError } from '#lib/api/client.ts';
import { LabApi } from '#lib/api/lab-client.ts';
import type { DeploymentCapabilities, LabStatus } from '#lib/api/types.ts';
import { apiFor } from '#lib/server/api.ts';
import { forwardingIdentity } from '#lib/server/forward.ts';
import { answeredWithProblems } from '#lib/server/problem.ts';
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
 * Which model Lab has resident, for a screen that reads it.
 *
 * A Lab that does not answer is not a page that will not load: the screen
 * still renders, says Lab is unreachable in Lab's own words, and the first
 * run tries again. Lab can take minutes to load its model, and an error
 * page for that would hide the explanation behind a retry button.
 */
export async function residentStatus(
	request: Request,
	fetch: typeof globalThis.fetch,
): Promise<{ status: LabStatus | null; statusProblem: { title: string; detail: string } | null }> {
	try {
		return { status: await labFor(request, fetch).status(), statusProblem: null };
	} catch (failure) {
		if (!(failure instanceof ApiError)) throw failure;
		return { status: null, statusProblem: { title: failure.title, detail: failure.message } };
	}
}

/**
 * Answer the browser with what Lab answered, problems included.
 *
 * A call that returns nothing is answered with no content, as Lab answered
 * it: `json(undefined)` would be a 200 with an empty body that is not JSON.
 */
export async function relay(
	event: RequestEvent,
	call: (lab: LabApi) => Promise<unknown>,
): Promise<Response> {
	return answered(event, async (lab) => {
		const answer = await call(lab);
		return answer === undefined ? new Response(null, { status: 204 }) : json(answer);
	});
}

/**
 * Pass Lab's event stream to the browser as it arrives.
 *
 * Piped rather than read here: a stream collected on the server would reach
 * the browser all at once, when the generation was over. A refusal comes
 * before the stream does, and is answered as a problem like any other.
 */
export function relayStream(
	event: RequestEvent,
	call: (lab: LabApi) => Promise<Response>,
): Promise<Response> {
	return answered(event, async (lab) => {
		const upstream = await call(lab);
		return new Response(upstream.body, {
			headers: {
				'Content-Type': 'text/event-stream',
				'Cache-Control': 'no-cache',
				// Several proxies buffer by default, which would hold every
				// token until the generation finished.
				'X-Accel-Buffering': 'no',
			},
		});
	});
}

async function answered(
	event: RequestEvent,
	respond: (lab: LabApi) => Promise<Response>,
): Promise<Response> {
	requireLab(await apiFor(event.request, event.fetch).capabilities());
	return answeredWithProblems(() => respond(labFor(event.request, event.fetch)));
}
