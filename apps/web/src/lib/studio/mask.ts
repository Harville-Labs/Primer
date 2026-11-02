/**
 * The inpainting mask: which pixels of the source image may change.
 *
 * Held at the source image's own resolution, whatever size it is drawn at,
 * so the mask sent is the one the backend needs without resampling: a
 * mask scaled up from a 600-pixel canvas has soft, shifted edges, and a
 * backend given one of another size refuses it or stretches it.
 *
 * Painted by hand rather than through a canvas library. A round brush, an
 * eraser and undo are a few dozen lines; Konva or Fabric would ship a scene
 * graph to every browser for them. The rasterising is here, as plain data,
 * so a test can say exactly which pixels a stroke marks.
 */

export interface Point {
	x: number;
	y: number;
}

/** The pixels a change touched, so only they are redrawn. */
export interface Box {
	left: number;
	top: number;
	right: number;
	bottom: number;
}

export interface Mask {
	width: number;
	height: number;
	/** One byte a pixel, row by row: 1 where the image may change. */
	data: Uint8Array;
}

export type Rgba = readonly [number, number, number, number];

export function blankMask(width: number, height: number): Mask {
	return { width, height, data: new Uint8Array(width * height) };
}

/**
 * Where on the image a point on the screen falls.
 *
 * `rect` is the element as drawn. Scaling by its size rather than the
 * image's is what keeps a stroke under the pointer when the image is shown
 * smaller than it is.
 */
export function toImagePoint(
	client: Point,
	rect: { left: number; top: number; width: number; height: number },
	mask: Pick<Mask, 'width' | 'height'>,
): Point {
	return {
		x: ((client.x - rect.left) * mask.width) / rect.width,
		y: ((client.y - rect.top) * mask.height) / rect.height,
	};
}

/**
 * Mark (value 1) or clear (value 0) every pixel whose centre is within
 * `radius` of the segment from `from` to `to`: a line with round ends, or a
 * dot when the two are the same. Null when the segment is off the image.
 */
export function stroke(
	mask: Mask,
	from: Point,
	to: Point,
	radius: number,
	value: 0 | 1,
): Box | null {
	const left = Math.max(0, Math.floor(Math.min(from.x, to.x) - radius));
	const top = Math.max(0, Math.floor(Math.min(from.y, to.y) - radius));
	const right = Math.min(mask.width, Math.ceil(Math.max(from.x, to.x) + radius));
	const bottom = Math.min(mask.height, Math.ceil(Math.max(from.y, to.y) + radius));
	if (left >= right || top >= bottom) return null;

	const dx = to.x - from.x;
	const dy = to.y - from.y;
	const length = dx * dx + dy * dy;
	const reach = radius * radius;
	for (let y = top; y < bottom; y++) {
		for (let x = left; x < right; x++) {
			const px = x + 0.5;
			const py = y + 0.5;
			// The nearest point on the segment, as a fraction along it.
			const t =
				length === 0
					? 0
					: Math.max(0, Math.min(1, ((px - from.x) * dx + (py - from.y) * dy) / length));
			const ox = px - (from.x + t * dx);
			const oy = py - (from.y + t * dy);
			if (ox * ox + oy * oy <= reach) mask.data[y * mask.width + x] = value;
		}
	}
	return { left, top, right, bottom };
}

export function isBlank(mask: Mask): boolean {
	return !mask.data.includes(1);
}

/** The whole mask as one box, for a redraw after undo or clear. */
export function whole(mask: Mask): Box {
	return { left: 0, top: 0, right: mask.width, bottom: mask.height };
}

/** RGBA pixels for `box`, `marked` where the mask is set and `clear` elsewhere. */
export function rgba(
	mask: Mask,
	box: Box,
	marked: Rgba,
	clear: Rgba,
): Uint8ClampedArray<ArrayBuffer> {
	const width = box.right - box.left;
	const pixels = new Uint8ClampedArray(width * (box.bottom - box.top) * 4);
	for (let y = box.top; y < box.bottom; y++) {
		for (let x = box.left; x < box.right; x++) {
			const colour = mask.data[y * mask.width + x] ? marked : clear;
			pixels.set(colour, ((y - box.top) * width + (x - box.left)) * 4);
		}
	}
	return pixels;
}

/**
 * How the mask is sent: transparent where the image may change, opaque
 * black where it may not.
 *
 * Transparency rather than white-on-black, because it is what both
 * backends read without a conversion. The OpenAI images API edits where a
 * mask is fully transparent, and ComfyUI's mask loaders, reading the alpha
 * channel, mark exactly those pixels.
 */
export const SENT: { marked: Rgba; clear: Rgba } = {
	marked: [0, 0, 0, 0],
	clear: [0, 0, 0, 255],
};

/** The mask as a base64 PNG at the image's resolution, for the request. */
export async function encodeMask(mask: Mask): Promise<string> {
	const canvas = document.createElement('canvas');
	canvas.width = mask.width;
	canvas.height = mask.height;
	const context = canvas.getContext('2d');
	if (!context) throw new Error('This browser cannot draw the mask.');
	const pixels = rgba(mask, whole(mask), SENT.marked, SENT.clear);
	context.putImageData(new ImageData(pixels, mask.width, mask.height), 0, 0);
	const blob = await new Promise<Blob | null>((done) => canvas.toBlob(done, 'image/png'));
	if (!blob) throw new Error('This browser could not encode the mask.');
	const bytes = new Uint8Array(await blob.arrayBuffer());
	let binary = '';
	for (let i = 0; i < bytes.length; i += 0x8000) {
		binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
	}
	return btoa(binary);
}
