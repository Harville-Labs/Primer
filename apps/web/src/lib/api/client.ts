import type {
	DeploymentCapabilities,
	DeploymentStatus,
	DocumentSummary,
	LibraryShare,
	LibrarySummary,
	Principal,
	ProblemDetail,
	ReindexSummary,
} from './types';

/**
 * Talking to Primer.
 *
 * Every call goes through this server, never from the browser to Control
 * directly: the identity headers Primer trusts are injected by the proxy in
 * front of it, and a browser that could reach Control could set them itself.
 */

export class ApiError extends Error {
	readonly status: number;
	readonly code: string;
	readonly requestId: string | null;

	constructor(problem: ProblemDetail) {
		// The server's own words, which are already written for a user.
		// Inventing a friendlier message here would hide the detail that
		// says which of several similar failures this was.
		super(problem.detail ?? problem.title);
		this.name = 'ApiError';
		this.status = problem.status;
		this.code = problem.code;
		this.requestId = problem.request_id;
	}
}

/**
 * Read an error body as a problem document, whatever it actually is.
 *
 * A body was previously cast to `ProblemDetail` and trusted. Anything that
 * did not match - a framework's own validation shape, a gateway's error page
 * rendered as JSON - therefore produced an `ApiError` with an undefined
 * status and a `detail` that was not a string, which reached the screen as
 * "[object Object]". The services all answer in the contract's shape now;
 * this is the guarantee that a service which does not cannot put nonsense in
 * front of a user.
 */
export function asProblem(body: unknown, status: number): ProblemDetail {
	const shape = (body ?? {}) as Record<string, unknown>;
	const detail = typeof shape.detail === 'string' ? shape.detail : null;
	return {
		code: typeof shape.code === 'string' ? shape.code : 'unexpected_response',
		title: typeof shape.title === 'string' ? shape.title : 'Unexpected response',
		// The response's own status, not the body's claim about it: the two
		// disagree only when the body is not what it says it is.
		status,
		detail: detail ?? `The server replied with ${status}.`,
		request_id: typeof shape.request_id === 'string' ? shape.request_id : null,
	} as ProblemDetail;
}

export interface ApiOptions {
	fetch?: typeof globalThis.fetch;
	baseUrl?: string;
}

export class PrimerApi {
	private readonly fetch: typeof globalThis.fetch;
	private readonly baseUrl: string;

	constructor(options: ApiOptions = {}) {
		this.fetch = options.fetch ?? globalThis.fetch;
		this.baseUrl = (options.baseUrl ?? '').replace(/\/$/, '');
	}

	private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
		const response = await this.fetch(`${this.baseUrl}${path}`, init);
		if (!response.ok) {
			throw new ApiError(await this.problemFrom(response));
		}
		if (response.status === 204) {
			return undefined as T;
		}
		return (await response.json()) as T;
	}

	private async problemFrom(response: Response): Promise<ProblemDetail> {
		try {
			return asProblem(await response.json(), response.status);
		} catch {
			// A proxy timeout or a crash produces a non-JSON body. Reporting
			// the status honestly beats a parse error the user cannot act on.
			return {
				code: 'unexpected_response',
				title: 'Unexpected response',
				status: response.status,
				detail: `The server replied with ${response.status}.`,
				request_id: null,
			};
		}
	}

	capabilities(): Promise<DeploymentCapabilities> {
		return this.request('/api/v1/capabilities');
	}

	/** Who the trusted proxy says is making this request. */
	me(): Promise<Principal> {
		return this.request('/api/v1/me');
	}

	/** How this deployment is wired. Administrators only; Control decides. */
	deploymentStatus(): Promise<DeploymentStatus> {
		return this.request('/api/v1/admin/status');
	}

	libraries(): Promise<LibrarySummary[]> {
		return this.request('/api/v1/libraries');
	}

	createLibrary(name: string): Promise<LibrarySummary> {
		return this.request('/api/v1/libraries', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ name }),
		});
	}

	/** Copy a library into a new one the caller owns. */
	duplicateLibrary(libraryId: string, name?: string): Promise<LibrarySummary> {
		return this.request(`/api/v1/libraries/${libraryId}/duplicate`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(name ? { name } : {}),
		});
	}

	/** Who the library is shared with. The owner's question; Control refuses anyone else. */
	shares(libraryId: string): Promise<LibraryShare[]> {
		return this.request(`/api/v1/libraries/${libraryId}/shares`);
	}

	shareLibrary(libraryId: string, email: string): Promise<LibraryShare> {
		return this.request(`/api/v1/libraries/${libraryId}/shares`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ email }),
		});
	}

	revokeShare(libraryId: string, userId: string): Promise<void> {
		return this.request(`/api/v1/libraries/${libraryId}/shares/${userId}`, { method: 'DELETE' });
	}

	deleteLibrary(libraryId: string): Promise<void> {
		return this.request(`/api/v1/libraries/${libraryId}`, { method: 'DELETE' });
	}

	documents(libraryId: string): Promise<DocumentSummary[]> {
		return this.request(`/api/v1/libraries/${libraryId}/documents`);
	}

	document(libraryId: string, documentId: string): Promise<DocumentSummary> {
		return this.request(`/api/v1/libraries/${libraryId}/documents/${documentId}`);
	}

	/**
	 * A document's bytes, as the response carrying them.
	 *
	 * The response rather than the body, because the caller is a proxy: it
	 * forwards the stream on without reading it, and needs the headers
	 * Control set to say what the bytes are and what they are called.
	 * Buffering here instead would hold a whole upload in memory to hand
	 * back something it already had.
	 */
	async content(libraryId: string, documentId: string): Promise<Response> {
		const response = await this.fetch(
			`${this.baseUrl}/api/v1/libraries/${libraryId}/documents/${documentId}/content`,
		);
		if (!response.ok) {
			throw new ApiError(await this.problemFrom(response));
		}
		return response;
	}

	/**
	 * Hand Control a multipart upload exactly as the browser sent it.
	 *
	 * The body is a stream and is passed on unread, with the browser's own
	 * Content-Type, which carries the multipart boundary. Parsing it into a
	 * form here and building a new one would hold the whole file in this
	 * server's memory first: one large upload, or a few at once, and the web
	 * tier needs as much memory as the biggest file anyone sends.
	 */
	upload(
		libraryId: string,
		body: ReadableStream<Uint8Array>,
		contentType: string,
	): Promise<DocumentSummary> {
		return this.request(`/api/v1/libraries/${libraryId}/documents`, {
			method: 'POST',
			body,
			headers: { 'content-type': contentType },
			// Required by fetch for a streamed request body.
			duplex: 'half',
		} as RequestInit);
	}

	/** Rebuild every document in a library, which is why anyone reindexes. */
	reindexLibrary(libraryId: string): Promise<ReindexSummary> {
		return this.request(`/api/v1/libraries/${libraryId}/documents/reindex`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: '{}',
		});
	}

	reindex(libraryId: string, documentId: string): Promise<DocumentSummary> {
		return this.request(`/api/v1/libraries/${libraryId}/documents/${documentId}/reindex`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: '{}',
		});
	}

	deleteDocument(libraryId: string, documentId: string): Promise<void> {
		return this.request(`/api/v1/libraries/${libraryId}/documents/${documentId}`, {
			method: 'DELETE',
		});
	}
}
