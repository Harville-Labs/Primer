import { ApiError, asProblem } from './client';
import type {
	ChatModelList,
	ConversationSummary,
	MessageSummary,
	PassagesResult,
	ProblemDetail,
	ProviderCheck,
	ProviderCreate,
	ProviderSummary,
	ProviderUpdate,
	ScoredText,
	ScoreRequest,
} from './types';

/**
 * Talking to Chat.
 *
 * Separate from `PrimerApi` because it is a separate service with its own
 * address: Control owns libraries and documents, Chat owns conversations.
 * Folding both into one client would mean one base URL for two deployments
 * that scale, fail, and are upgraded independently.
 *
 * Only the parts that are not a stream. Asking a question goes through
 * `/chat/ask`, which pipes the response through rather than collecting it.
 */
export interface ChatApiOptions {
	fetch?: typeof globalThis.fetch;
	baseUrl?: string;
}

/**
 * What a caller reads when Chat did not answer at all.
 *
 * Named for Chat for the reason Lab's is named for Lab: a transport error
 * reaching a catch-all reads as the same "something went wrong" every
 * unrelated bug produces. Usually a restart or a deploy in progress, so
 * trying again is the advice.
 */
export const CHAT_UNREACHABLE: ProblemDetail = {
	code: 'chat_unreachable',
	title: 'Chat is unreachable',
	status: 503,
	detail: 'The chat service did not answer. Try again in a minute.',
	request_id: null,
};

export class ChatApi {
	private readonly fetch: typeof globalThis.fetch;
	private readonly baseUrl: string;

	constructor(options: ChatApiOptions = {}) {
		this.fetch = options.fetch ?? globalThis.fetch;
		this.baseUrl = (options.baseUrl ?? '').replace(/\/$/, '');
	}

	private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
		let response: Response;
		try {
			response = await this.fetch(`${this.baseUrl}${path}`, init);
		} catch {
			// Only the transport failing lands here; an answer with an error
			// status is Chat's own words, read below.
			throw new ApiError(CHAT_UNREACHABLE);
		}
		if (!response.ok) {
			let problem: ProblemDetail;
			try {
				// Read rather than cast, so a body that is not the contract's
				// shape cannot reach a user as "[object Object]".
				problem = asProblem(await response.json(), response.status);
			} catch {
				problem = {
					code: 'unexpected_response',
					title: 'Unexpected response',
					status: response.status,
					detail: `Chat replied with ${response.status}.`,
					request_id: null,
				};
			}
			throw new ApiError(problem);
		}
		if (response.status === 204) return undefined as T;
		return (await response.json()) as T;
	}

	/**
	 * What can answer a question, and whether anything can.
	 *
	 * The reachability of the inference endpoint travels with the list
	 * rather than being inferred from its emptiness: those are different
	 * facts with different fixes, and a caller that guessed would tell an
	 * operator to restart the wrong thing.
	 */
	models(): Promise<ChatModelList> {
		return this.request('/api/v1/models');
	}

	/** Every endpoint this deployment can ask. Administrators only. */
	providers(): Promise<ProviderSummary[]> {
		return this.request('/api/v1/admin/providers');
	}

	addProvider(payload: ProviderCreate): Promise<ProviderSummary> {
		return this.request('/api/v1/admin/providers', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(payload),
		});
	}

	updateProvider(id: string, payload: ProviderUpdate): Promise<ProviderSummary> {
		return this.request(`/api/v1/admin/providers/${id}`, {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(payload),
		});
	}

	removeProvider(id: string): Promise<void> {
		return this.request(`/api/v1/admin/providers/${id}`, { method: 'DELETE' });
	}

	/** Ask an endpoint what it serves, now. */
	checkProvider(id: string): Promise<ProviderCheck> {
		return this.request(`/api/v1/admin/providers/${id}/check`, { method: 'POST' });
	}

	/**
	 * An endpoint's surprise at each token, from the top-k logprobs it
	 * returns. Refused before the endpoint is called when its provider has
	 * not declared that it returns them.
	 */
	score(payload: ScoreRequest): Promise<ScoredText> {
		return this.request('/api/v1/score', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(payload),
		});
	}

	/** The caller's own conversations, most recently updated first. */
	conversations(): Promise<ConversationSummary[]> {
		return this.request('/api/v1/conversations');
	}

	/**
	 * One conversation, asked for directly.
	 *
	 * Not found by searching the list. The list is loaded for the sidebar
	 * and can be stale or, on a transient failure, empty - and a thread that
	 * was just answered is exactly the one most likely to be missing from
	 * it. Searching it made a fresh answer look like a deleted conversation.
	 */
	conversation(conversationId: string): Promise<ConversationSummary> {
		return this.request(`/api/v1/conversations/${conversationId}`);
	}

	messages(conversationId: string): Promise<MessageSummary[]> {
		return this.request(`/api/v1/conversations/${conversationId}/messages`);
	}

	deleteConversation(conversationId: string): Promise<void> {
		return this.request(`/api/v1/conversations/${conversationId}`, { method: 'DELETE' });
	}

	/**
	 * A library's passages in reading order, for Lab's scan. Chat asks
	 * Control whether the caller may read the library before listing any.
	 * The id is encoded: it comes from the browser, and must not become a
	 * different path.
	 */
	passages(libraryId: string, limit: number): Promise<PassagesResult> {
		const library = encodeURIComponent(libraryId);
		return this.request(`/api/v1/libraries/${library}/passages?limit=${limit}`);
	}
}
