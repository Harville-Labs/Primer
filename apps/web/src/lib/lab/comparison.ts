import { parseEvents } from '#lib/api/sse.ts';
import { asFailure, problemOf } from './failure';
import { emptyGeneration, foldGeneration, type GenerationView } from './generation';

/**
 * Ask a Lab comparison route for its stream and show each step of it.
 *
 * Generating with edits and chatting with them answer with the same
 * events, so both read them here. `show` is called with a fresh view on
 * every change, never one written into, and is not called again once
 * `signal` has aborted: a run that was replaced must not draw over the one
 * that replaced it.
 */
export async function readComparison(
	fetch: typeof globalThis.fetch,
	path: string,
	body: unknown,
	signal: AbortSignal,
	show: (view: GenerationView) => void,
): Promise<void> {
	let view = emptyGeneration();
	show(view);
	try {
		const response = await fetch(path, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
			signal,
		});
		if (!response.ok) throw await problemOf(response);
		if (!response.body) throw new Error('The generation came back without a stream.');
		for await (const event of parseEvents(response.body)) {
			if (signal.aborted) return;
			view = foldGeneration(view, event);
			show(view);
		}
		if (!view.completed && !view.failure) {
			show({
				...view,
				failure: {
					title: 'The generation stopped',
					detail: 'The stream ended before Lab finished. Run it again.',
				},
			});
		}
	} catch (failure) {
		if (signal.aborted) return;
		show({ ...view, ahead: null, failure: asFailure(failure) });
	}
}
