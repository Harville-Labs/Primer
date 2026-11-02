import { eventStream } from '#lib/server/stream.ts';
import { studioAnswer } from '#lib/server/studio.ts';
import type { RequestHandler } from './$types';

/**
 * Make images, with progress as it comes.
 *
 * The body is passed on as the browser sent it. Control validates it against
 * the contract and Chat against what the provider declared, so a second
 * check here would be a third copy of the rules to keep in step.
 */
export const POST: RequestHandler = (event) =>
	studioAnswer(event, async (control) =>
		eventStream((await control.generate(await event.request.json())).body),
	);
