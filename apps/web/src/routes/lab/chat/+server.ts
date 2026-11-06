import type { SteeredChatRequest } from '#lib/api/types.ts';
import { relayStream } from '#lib/server/lab.ts';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async (event) => {
	const body = (await event.request.json()) as SteeredChatRequest;
	return relayStream(event, (lab) => lab.chat(body));
};
