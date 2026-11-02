<script lang="ts">
	import { Brush, Eraser, Trash2, Undo2 } from '@lucide/svelte';
	import { Button, Shortcut, Slider, Toggle } from '@sivir-ui/svelte';
	import type { StudioImage } from '#lib/api/types.ts';
	import { type Box, type Rgba, rgba, toImagePoint, whole } from '#lib/studio/mask.ts';
	import { BRUSH, type Painter } from '#lib/studio/painter.svelte.ts';

	/**
	 * The source image with a mask painted over it.
	 *
	 * The canvas is the image's own size and scaled by CSS to fit, so what
	 * is painted is already at the resolution sent; only the pixels a
	 * stroke touched are redrawn.
	 */
	interface Props {
		image: StudioImage;
		painter: Painter;
		disabled: boolean;
	}

	let { image, painter, disabled }: Props = $props();

	// The brand accent at half strength, so the image shows through the mark.
	const SHOWN: { marked: Rgba; clear: Rgba } = {
		marked: [236, 72, 153, 128],
		clear: [0, 0, 0, 0],
	};

	let canvas = $state<HTMLCanvasElement>();
	let painting = false;

	function draw(box: Box | null) {
		const context = canvas?.getContext('2d');
		if (!box || !context) return;
		const pixels = rgba(painter.mask, box, SHOWN.marked, SHOWN.clear);
		context.putImageData(
			new ImageData(pixels, box.right - box.left, box.bottom - box.top),
			box.left,
			box.top,
		);
	}

	$effect(() => {
		// A new canvas, as when the source changes, starts from the mask as it is.
		if (canvas) draw(whole(painter.mask));
	});

	function at(event: PointerEvent) {
		return toImagePoint(
			{ x: event.clientX, y: event.clientY },
			(event.currentTarget as HTMLCanvasElement).getBoundingClientRect(),
			painter.mask,
		);
	}

	function down(event: PointerEvent) {
		if (disabled || event.button !== 0) return;
		(event.currentTarget as HTMLCanvasElement).setPointerCapture(event.pointerId);
		painting = true;
		draw(painter.begin(at(event)));
	}

	function move(event: PointerEvent) {
		if (painting) draw(painter.move(at(event)));
	}

	function up() {
		if (!painting) return;
		painting = false;
		painter.end();
	}

	/** Shortcuts, except while typing: the prompt box takes `[` and `]` too. */
	function key(event: KeyboardEvent) {
		const target = event.target as HTMLElement | null;
		if (disabled || target?.closest('input, textarea, select, [contenteditable="true"]')) return;
		if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
			event.preventDefault();
			draw(painter.undo());
		} else if (event.key === '[') painter.resize(-1);
		else if (event.key === ']') painter.resize(1);
		else if (event.key.toLowerCase() === 'e' && !event.metaKey && !event.ctrlKey) {
			painter.erasing = !painter.erasing;
		}
	}
</script>

<svelte:window onkeydown={key} />

<div class="flex flex-col gap-3">
	<div role="toolbar" aria-label="Mask" class="flex flex-wrap items-center gap-2">
		<Toggle
			pressed={!painter.erasing}
			onPressedChange={() => (painter.erasing = false)}
			size="sm"
			aria-label="Brush"
			{disabled}
		>
			<Brush size={13} aria-hidden="true" />
		</Toggle>
		<Toggle
			pressed={painter.erasing}
			onPressedChange={() => (painter.erasing = true)}
			size="sm"
			aria-label="Eraser"
			{disabled}
		>
			<Eraser size={13} aria-hidden="true" />
		</Toggle>
		<div class="w-40">
			<Slider
				bind:value={painter.size}
				min={BRUSH.min}
				max={BRUSH.max}
				step={BRUSH.step}
				label="Brush"
				{disabled}
			/>
		</div>
		<Button
			variant="ghost"
			size="sm"
			onclick={() => draw(painter.undo())}
			disabled={disabled || painter.undoable === 0}
		>
			<Undo2 size={13} aria-hidden="true" />
			Undo
		</Button>
		<Button
			variant="ghost"
			size="sm"
			onclick={() => draw(painter.clear())}
			disabled={disabled || painter.blank}
		>
			<Trash2 size={13} aria-hidden="true" />
			Clear
		</Button>
	</div>

	<div
		class="relative mx-auto max-h-[65vh] w-full overflow-hidden rounded-lg border border-border bg-muted"
		style:aspect-ratio="{image.width} / {image.height}"
		style:max-width="calc(65vh * {image.width / image.height})"
	>
		<img
			src="/studio/images/{image.id}/content"
			alt={image.provenance?.prompt ?? 'The image being edited'}
			class="absolute inset-0 size-full object-fill"
		/>
		<canvas
			bind:this={canvas}
			width={image.width}
			height={image.height}
			onpointerdown={down}
			onpointermove={move}
			onpointerup={up}
			onpointercancel={up}
			aria-label="Mask over the image"
			aria-describedby="mask-help"
			class="absolute inset-0 size-full touch-none {painter.erasing ? 'cursor-cell' : 'cursor-crosshair'}"
		></canvas>
	</div>

	<p id="mask-help" class="text-[11px] leading-relaxed text-muted-foreground">
		Paint over what should change; the rest is kept.
		<Shortcut shortcut="e" /> brush or eraser, <Shortcut shortcut="[" /> and
		<Shortcut shortcut="]" /> brush size, <Shortcut shortcut="cmd+z" /> undo.
	</p>
</div>
