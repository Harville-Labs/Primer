import { json } from '@sveltejs/kit';
import type { ScoreRequest } from '#lib/api/types.ts';
import { chatFor } from '#lib/server/chat.ts';
import { answeredWithProblems } from '#lib/server/problem.ts';
import type { RequestHandler } from './$types';

/**
 * Scoring text against an operator's endpoint, for the Anomalies tab.
 *
 * Chat's refusal is passed through whole: "this provider does not declare
 * logprobs" and "the endpoint is unreachable" have different fixes, and
 * the title is what tells the reader which one they have.
 */
export const POST: RequestHandler = async ({ request, fetch }) => {
	const body = (await request.json()) as ScoreRequest;
	return answeredWithProblems(async () => json(await chatFor(request, fetch).score(body)));
};
