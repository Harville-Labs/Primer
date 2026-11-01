import { PrimerApi } from '#lib/api/client.ts';
import { forwardingIdentity } from '#lib/server/forward.ts';
import { PRIMER_CONTROL_URL } from '$app/env/private';

/** The server's connection to Control, as the user the request is from. */
export function apiFor(request: Request, fetch: typeof globalThis.fetch): PrimerApi {
	return new PrimerApi({ baseUrl: PRIMER_CONTROL_URL, fetch: forwardingIdentity(request, fetch) });
}
