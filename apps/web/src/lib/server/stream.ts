/**
 * A service's event stream, handed to the browser as it arrives.
 *
 * Piped rather than read here: a stream collected on the server would reach
 * the browser all at once, when the work was over. Only for a stream that
 * opened: a refusal is a problem document, and labelling one an event stream
 * leaves the browser waiting for events that are never coming.
 */
export function eventStream(body: ReadableStream<Uint8Array> | null): Response {
	return new Response(body, {
		headers: {
			'Content-Type': 'text/event-stream',
			'Cache-Control': 'no-cache',
			// Several proxies buffer by default, which would hold every event
			// until the stream finished.
			'X-Accel-Buffering': 'no',
		},
	});
}
