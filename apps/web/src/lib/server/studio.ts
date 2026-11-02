import { error, json, type RequestEvent } from '@sveltejs/kit';
import type { PrimerApi } from '#lib/api/client.ts';
import type { DeploymentCapabilities } from '#lib/api/types.ts';
import { apiFor } from '#lib/server/api.ts';
import { answeredWithProblems } from '#lib/server/problem.ts';

/**
 * Refuse every Studio route on a deployment without it.
 *
 * Pages and endpoints alike, as `requireLab` does, so a hidden link is
 * never the only thing between a person and a feature the operator left
 * off. Control refuses too; this is what makes the answer a 404 rather
 * than a page that loads and then fails.
 */
export function requireStudio(capabilities: DeploymentCapabilities): void {
	if (!capabilities.studio_available) error(404, 'This deployment has no Studio.');
}

/**
 * Answer the browser with what Control answered for a Studio call,
 * problems included, once Studio is known to be on.
 */
export async function studioAnswer(
	event: RequestEvent,
	respond: (control: PrimerApi) => Promise<Response>,
): Promise<Response> {
	const control = apiFor(event.request, event.fetch);
	requireStudio(await control.capabilities());
	return answeredWithProblems(() => respond(control));
}

/** As `studioAnswer`, for a call whose answer is JSON or nothing. */
export function studioRelay(
	event: RequestEvent,
	call: (control: PrimerApi) => Promise<unknown>,
): Promise<Response> {
	return studioAnswer(event, async (control) => {
		const answer = await call(control);
		return answer === undefined ? new Response(null, { status: 204 }) : json(answer);
	});
}
