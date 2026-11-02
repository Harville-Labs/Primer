import { type Box, blankMask, isBlank, type Mask, type Point, stroke, whole } from './mask';

/** How many strokes back undo reaches. Each is a copy of the whole mask. */
const UNDO_DEPTH = 20;

export const BRUSH = { min: 4, max: 256, step: 4, initial: 48 } as const;

/**
 * The brush, the eraser and undo, over a mask at the image's resolution.
 *
 * Each change returns the box it touched, and the canvas showing the mask
 * redraws that much: redrawing a whole 2048-pixel mask on every pointer
 * move is what makes a brush lag behind the hand.
 */
export class Painter {
	/** The brush's diameter, in the image's pixels. */
	size = $state<number>(BRUSH.initial);
	erasing = $state(false);
	blank = $state(true);
	undoable = $state(0);

	mask: Mask;
	private undos: Uint8Array[] = [];
	private last: Point | null = null;

	constructor(width: number, height: number) {
		this.mask = blankMask(width, height);
	}

	private get value(): 0 | 1 {
		return this.erasing ? 0 : 1;
	}

	begin(point: Point): Box | null {
		this.undos = [...this.undos, this.mask.data.slice()].slice(-UNDO_DEPTH);
		this.undoable = this.undos.length;
		this.last = point;
		return stroke(this.mask, point, point, this.size / 2, this.value);
	}

	move(point: Point): Box | null {
		if (!this.last) return null;
		const touched = stroke(this.mask, this.last, point, this.size / 2, this.value);
		this.last = point;
		return touched;
	}

	end(): void {
		if (!this.last) return;
		this.last = null;
		this.blank = isBlank(this.mask);
	}

	undo(): Box | null {
		const previous = this.undos.at(-1);
		if (!previous) return null;
		this.undos = this.undos.slice(0, -1);
		this.undoable = this.undos.length;
		this.mask.data.set(previous);
		this.blank = isBlank(this.mask);
		return whole(this.mask);
	}

	/** Clearing can be undone, like any stroke. */
	clear(): Box {
		if (!this.blank) {
			this.undos = [...this.undos, this.mask.data.slice()].slice(-UNDO_DEPTH);
			this.undoable = this.undos.length;
		}
		this.mask.data.fill(0);
		this.blank = true;
		return whole(this.mask);
	}

	resize(by: 1 | -1): void {
		this.size = Math.min(BRUSH.max, Math.max(BRUSH.min, this.size + by * BRUSH.step));
	}
}
