import type { ConversationSummary } from '#lib/api/types.ts';
import { apiFor } from '#lib/server/api.ts';
import { chatFor } from '#lib/server/chat.ts';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ request, fetch }) => {
	const api = apiFor(request, fetch);
	const [capabilities, libraries, principal] = await Promise.all([
		api.capabilities(),
		api.libraries(),
		api.me().catch(() => null),
	]);

	// Conversations belong to the frame rather than to the chat screen: the
	// sidebar lists them from wherever you are, the same way it lists
	// libraries. Asked for only when this deployment has a Chat to ask, and
	// an empty list rather than a broken page when it will not answer -
	// nothing here is worth failing a page load over.
	const conversations: ConversationSummary[] = capabilities.chat_available
		? await chatFor(request, fetch)
				.conversations()
				.catch(() => [])
		: [];

	return { capabilities, libraries, principal, conversations };
};
