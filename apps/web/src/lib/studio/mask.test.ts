import { describe, expect, it } from 'vitest';
import { blankMask, rgba, SENT, stroke, toImagePoint, whole } from './mask';
import { Painter } from './painter.svelte';

function marked(mask: { width: number; data: Uint8Array }): string[] {
	return [...mask.data.keys()]
		.filter((index) => mask.data[index])
		.map((index) => `${index % mask.width},${Math.floor(index / mask.width)}`);
}

describe('the mask', () => {
	it('marks the pixels a stroke covers, with round ends', () => {
		const mask = blankMask(8, 4);

		stroke(mask, { x: 2, y: 2 }, { x: 6, y: 2 }, 1, 1);

		// Pixel centres within one pixel of the line from (2,2) to (6,2).
		expect(marked(mask)).toEqual([
			'1,1',
			'2,1',
			'3,1',
			'4,1',
			'5,1',
			'6,1',
			'1,2',
			'2,2',
			'3,2',
			'4,2',
			'5,2',
			'6,2',
		]);
	});

	it('paints a dot round, leaving the corners of its box alone', () => {
		const mask = blankMask(8, 8);

		const box = stroke(mask, { x: 4, y: 4 }, { x: 4, y: 4 }, 2, 1);

		// The box is 4 by 4; its corner centres are 2.1 from the middle, outside the brush.
		expect(box).toEqual({ left: 2, top: 2, right: 6, bottom: 6 });
		expect(marked(mask)).toHaveLength(12);
		for (const corner of ['2,2', '5,2', '2,5', '5,5']) expect(marked(mask)).not.toContain(corner);
	});

	it('is the size of the image, whatever size it is drawn at', () => {
		const mask = blankMask(2048, 1024);
		// Shown at 512 wide: the middle of what is drawn is the middle of the image.
		const point = toImagePoint(
			{ x: 10 + 256, y: 20 + 128 },
			{ left: 10, top: 20, width: 512, height: 256 },
			mask,
		);

		expect(point).toEqual({ x: 1024, y: 512 });
		stroke(mask, point, point, 2, 1);
		const pixels = rgba(mask, whole(mask), SENT.marked, SENT.clear);
		expect(pixels.length).toBe(2048 * 1024 * 4);
	});

	it('is sent transparent where the image may change and opaque black elsewhere', () => {
		const mask = blankMask(3, 1);
		stroke(mask, { x: 1.5, y: 0.5 }, { x: 1.5, y: 0.5 }, 0.5, 1);

		expect([...rgba(mask, whole(mask), SENT.marked, SENT.clear)]).toEqual([
			0, 0, 0, 255, 0, 0, 0, 0, 0, 0, 0, 255,
		]);
	});

	it('ignores a stroke that is off the image', () => {
		const mask = blankMask(4, 4);

		expect(stroke(mask, { x: -20, y: -20 }, { x: -10, y: -10 }, 2, 1)).toBeNull();
		expect(marked(mask)).toEqual([]);
	});
});

describe('the brush', () => {
	it('erases what it painted, and says when nothing is left', () => {
		const painter = new Painter(8, 8);
		painter.size = 4;
		painter.begin({ x: 4, y: 4 });
		painter.end();
		expect(painter.blank).toBe(false);

		painter.erasing = true;
		painter.size = 8;
		painter.begin({ x: 4, y: 4 });
		painter.end();

		expect(painter.blank).toBe(true);
	});

	it('undoes a stroke, and a clear, one at a time', () => {
		const painter = new Painter(8, 8);
		painter.begin({ x: 1, y: 1 });
		painter.move({ x: 6, y: 1 });
		painter.end();
		const painted = painter.mask.data.slice();
		painter.clear();

		painter.undo();
		expect(painter.mask.data).toEqual(painted);
		painter.undo();
		expect(painter.blank).toBe(true);
		expect(painter.undoable).toBe(0);
	});

	it('keeps the brush between its smallest and largest', () => {
		const painter = new Painter(8, 8);
		painter.size = 4;
		painter.resize(-1);
		expect(painter.size).toBe(4);

		painter.size = 256;
		painter.resize(1);
		expect(painter.size).toBe(256);
	});
});
