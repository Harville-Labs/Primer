<script lang="ts">
	// Illustrative values in the shape of a real reading: rows are layers,
	// first at the bottom as the Grid draws them; shade is the lens's
	// confidence in its own top token.
	const tokens = ['The', ' capital', ' of', ' France', ' is'];
	const rows = [
		{ layer: 'L5', cells: [['.', 0.9], [' city', 0.55], [' the', 0.62], [' Paris', 0.71], [' Paris', 0.93]] },
		{ layer: 'L4', cells: [[',', 0.4], [' city', 0.42], [' the', 0.48], [',', 0.38], [' Paris', 0.66]] },
		{ layer: 'L3', cells: [[',', 0.2], [' of', 0.31], [' the', 0.3], [',', 0.22], [' a', 0.28]] },
		{ layer: 'L2', cells: [[' ', 0.12], [' of', 0.18], [' a', 0.14], [' and', 0.1], [' a', 0.15]] },
		{ layer: 'L1', cells: [[' ', 0.06], [',', 0.07], [' a', 0.05], [' ', 0.05], [' the', 0.08]] },
	] as const;
	const cell = { w: 92, h: 30, x0: 44, y0: 8 };
</script>

<svg viewBox="0 0 520 186" class="mx-auto block h-auto w-full max-w-xl" role="img" aria-labelledby="lensgrid-title">
	<title id="lensgrid-title">A small grid of logit-lens readings, layers by tokens</title>
	{#each rows as row, r (row.layer)}
		<text x="34" y={cell.y0 + r * cell.h + 19} text-anchor="end" class="fill-muted-foreground font-mono text-[10px]">
			{row.layer}
		</text>
		{#each row.cells as [guess, p], c (c)}
			<rect
				x={cell.x0 + c * cell.w}
				y={cell.y0 + r * cell.h}
				width={cell.w - 2}
				height={cell.h - 2}
				rx="3"
				class="fill-lab-positive"
				fill-opacity={0.08 + 0.92 * p}
			/>
			<text
				x={cell.x0 + c * cell.w + (cell.w - 2) / 2}
				y={cell.y0 + r * cell.h + 18}
				text-anchor="middle"
				class="font-mono text-[10px] {p > 0.5 ? 'fill-on-lab' : 'fill-foreground'}"
			>
				{guess.trim() || '␣'}
			</text>
		{/each}
	{/each}
	{#each tokens as token, c (c)}
		<text
			x={cell.x0 + c * cell.w + (cell.w - 2) / 2}
			y={cell.y0 + rows.length * cell.h + 18}
			text-anchor="middle"
			class="fill-foreground text-[11px]"
		>
			{token.trim()}
		</text>
	{/each}
</svg>
