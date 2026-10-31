import { describe, expect, it } from 'vitest';
import { eachLimited } from './concurrency';

/** A task that stays in flight until the test lets it finish. */
function gate() {
	let open = () => {};
	const opened = new Promise<void>((resolve) => {
		open = resolve;
	});
	return { opened, open };
}

describe('eachLimited', () => {
	it('never has more than the limit in flight', async () => {
		let inFlight = 0;
		let peak = 0;
		const started: number[] = [];

		await eachLimited([1, 2, 3, 4, 5, 6, 7], 3, async (item) => {
			started.push(item);
			inFlight += 1;
			peak = Math.max(peak, inFlight);
			await Promise.resolve();
			inFlight -= 1;
		});

		expect(started).toEqual([1, 2, 3, 4, 5, 6, 7]);
		expect(peak).toBe(3);
	});

	it('starts the next item as soon as any one finishes', async () => {
		// A slow first file must not hold back the rest: that is the whole
		// difference between this and uploading in batches.
		const slow = gate();
		const started: string[] = [];

		const done = eachLimited(['slow', 'b', 'c', 'd'], 2, async (item) => {
			started.push(item);
			if (item === 'slow') await slow.opened;
		});
		await new Promise((resolve) => setTimeout(resolve, 0));

		expect(started).toEqual(['slow', 'b', 'c', 'd']);
		slow.open();
		await done;
	});

	it('finishes immediately with nothing to do', async () => {
		await expect(eachLimited([], 4, async () => {})).resolves.toBeUndefined();
	});
});
