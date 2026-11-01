import { json } from '@sveltejs/kit';
import { LISTING } from '#lib/lab/scan.ts';
import { apiFor } from '#lib/server/api.ts';
import { chatFor } from '#lib/server/chat.ts';
import { requireLab } from '#lib/server/lab.ts';
import { answeredWithProblems } from '#lib/server/problem.ts';
import type { RequestHandler } from './$types';

/**
 * A library's passages, for the Scan tab to hand to Lab a batch at a time.
 *
 * Read from Chat, as the person, rather than by Lab: Chat asks Control
 * whether they may read the library, which is the one place that decides,
 * and Lab is never given a way to read a library of its own. Under `/lab`
 * and gated on it, because the scan is its only reader.
 */
export const GET: RequestHandler = async ({ params, request, fetch }) => {
	requireLab(await apiFor(request, fetch).capabilities());
	return answeredWithProblems(async () =>
		json(await chatFor(request, fetch).passages(params.libraryId, LISTING)),
	);
};
