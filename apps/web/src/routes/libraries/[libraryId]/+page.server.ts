import { error, fail } from '@sveltejs/kit';
import { ApiError } from '#lib/api/client.ts';
import type { GraphReading } from '#lib/graph-status.ts';
import { owns } from '#lib/libraries.ts';
import { apiFor } from '#lib/server/api.ts';
import { chatFor } from '#lib/server/chat.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * The library's graph coverage, or what stopped it being read.
 *
 * Not awaited by the load: SvelteKit streams it, so the document list is
 * on screen while Retrieval counts the graph and groups it into topics,
 * which on a large library takes noticeably longer than listing documents.
 */
async function readGraph(
	chat: ReturnType<typeof chatFor>,
	libraryId: string,
): Promise<GraphReading> {
	try {
		return { status: await chat.graphStatus(libraryId) };
	} catch (cause) {
		// Shown in the graph card, not thrown: a graph that cannot be read
		// is no reason to lose the document list beside it.
		if (cause instanceof ApiError) return { error: cause.message };
		throw cause;
	}
}

export const load: PageServerLoad = async ({ params, parent, request, fetch }) => {
	const api = apiFor(request, fetch);
	const graph = readGraph(chatFor(request, fetch), params.libraryId);
	try {
		// The library comes from the list the layout already fetched, so
		// opening a document list does not re-fetch every library to name one.
		const [{ libraries, principal }, documents] = await Promise.all([
			parent(),
			api.documents(params.libraryId),
		]);
		const library = libraries.find((candidate) => candidate.id === params.libraryId);
		// The server already answered 404 for a library that is not ours;
		// this covers the narrow case of it being deleted between calls.
		if (!library) error(404, 'Library not found');

		// Who may change who else can see this. Decided from the owner rather
		// than from whether the shares call happens to succeed, and false
		// when the principal is unknown - the layout tolerates `/me` failing,
		// and an unknown caller is not an owner. Control refuses either way;
		// this only decides what is worth putting on screen.
		const owned = owns(principal, library);
		return {
			library,
			documents,
			graph,
			owned,
			shares: owned ? await api.shares(params.libraryId) : [],
		};
	} catch (cause) {
		if (cause instanceof ApiError) error(cause.status, cause.message);
		throw cause;
	}
};

export const actions: Actions = {
	reindex: async ({ params, request, fetch }) => {
		const form = await request.formData();
		try {
			await apiFor(request, fetch).reindex(params.libraryId, String(form.get('id')));
		} catch (cause) {
			if (cause instanceof ApiError) return fail(cause.status, { error: cause.message });
			throw cause;
		}
		return { reindexed: true };
	},

	// Rebuilding the whole library, which is the reason anyone reindexes:
	// the chunker or the embedding model changed and everything indexed
	// under the old settings is stale together. Control decides which
	// documents are actually startable, and reports the ones it left alone
	// rather than treating them as failures.
	reindexAll: async ({ params, request, fetch }) => {
		try {
			const summary = await apiFor(request, fetch).reindexLibrary(params.libraryId);
			return { rebuilt: summary };
		} catch (cause) {
			if (cause instanceof ApiError) return fail(cause.status, { error: cause.message });
			throw cause;
		}
	},

	delete: async ({ params, request, fetch }) => {
		const form = await request.formData();
		try {
			await apiFor(request, fetch).deleteDocument(params.libraryId, String(form.get('id')));
		} catch (cause) {
			if (cause instanceof ApiError) return fail(cause.status, { error: cause.message });
			throw cause;
		}
		return { deleted: true };
	},

	// Sharing reports its failures under their own key. Both forms live on
	// this page, and one `error` shared between them would print "no such
	// user" over the document table.
	share: async ({ params, request, fetch }) => {
		const form = await request.formData();
		const email = String(form.get('email') ?? '').trim();
		if (!email) return fail(422, { shareError: 'Enter the address to share with.' });
		try {
			await apiFor(request, fetch).shareLibrary(params.libraryId, email);
		} catch (cause) {
			if (cause instanceof ApiError) return fail(cause.status, { shareError: cause.message });
			throw cause;
		}
		return { shared: email };
	},

	revoke: async ({ params, request, fetch }) => {
		const form = await request.formData();
		try {
			await apiFor(request, fetch).revokeShare(params.libraryId, String(form.get('userId')));
		} catch (cause) {
			if (cause instanceof ApiError) return fail(cause.status, { shareError: cause.message });
			throw cause;
		}
		return { revoked: true };
	},
};
