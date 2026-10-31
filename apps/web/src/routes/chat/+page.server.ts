import { ApiError } from '#lib/api/client.ts';
import type { ChatModelList, ConversationSummary, MessageSummary } from '#lib/api/types.ts';
import { chatFor } from '#lib/server/chat.ts';
import type { PageServerLoad } from './$types';

/**
 * Why nothing can answer a question, or null when something can.
 *
 * Keyed on there being no models rather than on reachability, so this can
 * never disagree with the composer, which is enabled by the same fact. An
 * endpoint that is unreachable but somehow still listed a model is a working
 * deployment, and warning about it would be crying wolf.
 *
 * `endpoint_reachable` is read as true when absent. It is a newer field than
 * the rest of this response, and a web app rolled ahead of its Chat would
 * otherwise put a red banner across a deployment with nothing wrong with it.
 */
function whyUnanswerable(body: ChatModelList): string | null {
	if ((body.models ?? []).length > 0) return null;
	if (body.detail) return body.detail;
	return body.endpoint_reachable === false
		? 'The chat endpoint could not be reached.'
		: 'This deployment is serving no models.';
}

/** Matched before an id is put in a path, so nothing else can be. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The chat screen: which models are offered, and which conversation is
 * being read, if any.
 *
 * The conversation is a search parameter rather than page state, so a thread
 * survives a refresh and can be linked to. Which one is open is exactly the
 * kind of thing a URL is for.
 *
 * A failure here is not a failure of the page: an empty list means no
 * history rather than a chat screen that will not load.
 *
 * Having no model to answer with is different, and is not smoothed over.
 * `modelsProblem` carries the reason - Chat itself unreachable, or Chat
 * reachable and the inference endpoint behind it not - so the screen can say
 * which. Offering a model that nothing is serving would be worse than
 * offering none: it reads as a working deployment right up until someone
 * asks a question.
 *
 * What is not swallowed is why a named conversation did not open. A thread
 * that silently appears blank looks like a thread that lost its messages, so
 * the reason is returned and said on the page.
 */
export const load: PageServerLoad = async ({ request, fetch, url }) => {
	const chat = chatFor(request, fetch);
	const asked = url.searchParams.get('conversation');
	const wanted = asked && UUID.test(asked) ? asked : null;

	// The sidebar's list is loaded by the layout and is not read here. This
	// page asks for the one conversation it needs by id, so a stale or
	// briefly empty list cannot make an open thread look deleted.
	const modelsResult = await chat
		.models()
		.then((body) => ({ models: body.models ?? [], problem: whyUnanswerable(body) }))
		.catch(() => ({
			models: [] as ChatModelList['models'],
			problem: 'The chat service could not be reached, so no model can answer.',
		}));
	const { models, problem: modelsProblem } = modelsResult;

	let opened: { conversation: ConversationSummary; messages: MessageSummary[] } | null = null;
	//: Why the conversation in the URL is not the one on screen: `missing` for
	//: one that is gone or was never this caller's, `unavailable` when Chat
	//: would not give up its messages.
	let unopened: 'missing' | 'unavailable' | null = null;

	if (wanted) {
		// Asked for directly rather than found in the list above. That list
		// is loaded for the sidebar and can be stale, or empty after a
		// transient failure - and the thread most likely to be missing from
		// it is the one just answered, which is precisely the one on screen.
		const [conversation, messages] = await Promise.all([
			chat.conversation(wanted).catch((error) => (error instanceof ApiError ? error : null)),
			chat.messages(wanted).catch((): MessageSummary[] | null => null),
		]);
		if (conversation instanceof ApiError) {
			// 404 is a thread that is genuinely gone; anything else is Chat
			// being unable to answer, which is temporary and worded as such.
			unopened = conversation.status === 404 ? 'missing' : 'unavailable';
		} else if (conversation && messages) {
			opened = { conversation, messages };
		} else {
			unopened = 'unavailable';
		}
	}

	return { models, modelsProblem, opened, unopened };
};
