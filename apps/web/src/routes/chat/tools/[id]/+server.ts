import { json } from '@sveltejs/kit';
import type { ToolDecision } from '#lib/api/chat-client.ts';
import { chatFor } from '#lib/server/chat.ts';
import { answeredWithProblems } from '#lib/server/problem.ts';
import type { RequestHandler } from './$types';

/**
 * A user's decision on a tool call, passed to Chat.
 *
 * The answer that asked for the call is still streaming, waiting on this:
 * Chat polls for the decision and carries on once it lands. Whether this
 * user may decide this call is Chat's to say, from the conversation it
 * belongs to - nothing here checks ownership, because nothing here could
 * do it without asking Chat anyway.
 */

const DECISIONS: readonly ToolDecision[] = ['approve', 'deny'];

export const POST: RequestHandler = async ({ request, params, fetch }) => {
	const { decision } = (await request.json()) as { decision?: unknown };
	// Allow-listed, because the decision becomes part of a path on Chat.
	if (!DECISIONS.includes(decision as ToolDecision)) {
		return json(
			{
				code: 'INVALID_DECISION',
				title: 'Not a decision',
				status: 400,
				detail: 'A tool call is approved or denied, and nothing else.',
				request_id: null,
			},
			{ status: 400 },
		);
	}
	return answeredWithProblems(async () =>
		json(await chatFor(request, fetch).decideTool(params.id, decision as ToolDecision)),
	);
};
