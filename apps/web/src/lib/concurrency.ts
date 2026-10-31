/**
 * Run `work` over `items` with at most `limit` in flight at once.
 *
 * Uploads go through this rather than one at a time or all at once. One at
 * a time leaves the connection idle while each file is stored and queued,
 * which is most of the wait for a folder of small files. All at once opens a
 * request per file - hundreds for a dropped folder - and the browser queues
 * all but a handful anyway, while each holds a body the server has to buffer.
 *
 * Items start in order, and `work` is expected to handle its own failures:
 * one file being refused must not stop the rest.
 */
export async function eachLimited<T>(
	items: readonly T[],
	limit: number,
	work: (item: T) => Promise<void>,
): Promise<void> {
	let next = 0;
	const worker = async () => {
		while (next < items.length) {
			const item = items[next];
			next += 1;
			await work(item);
		}
	};
	await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
}

/**
 * How many files upload at once.
 *
 * Browsers allow six connections per origin over HTTP/1.1; leaving two free
 * keeps the page able to poll status and navigate while a folder uploads.
 */
export const UPLOAD_CONCURRENCY = 4;
