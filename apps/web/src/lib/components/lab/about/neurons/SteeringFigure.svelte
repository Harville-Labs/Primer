<script lang="ts">
	// Two clouds of residual vectors, drawn in two of their thousands of
	// dimensions. The points are fixed rather than random so the figure is
	// the same on every load.
	const positive = [
		[318, 64], [342, 82], [300, 90], [356, 58], [330, 104], [372, 88], [310, 70], [348, 112], [384, 70], [326, 46],
	];
	const negative = [
		[118, 170], [142, 188], [100, 196], [156, 162], [130, 210], [168, 192], [110, 176], [150, 214], [182, 178], [124, 150],
	];
	const mean = (points: number[][]) =>
		[0, 1].map((axis) => points.reduce((sum, point) => sum + point[axis], 0) / points.length);
	const [px, py] = mean(positive);
	const [nx, ny] = mean(negative);
	const probe = { x: 214, y: 132 };
	const alpha = 0.6;
</script>

<svg viewBox="0 0 480 250" class="mx-auto block h-auto w-full max-w-lg" role="img" aria-labelledby="steering-title">
	<title id="steering-title">A steering vector as the difference between two means</title>
	<defs>
		<marker id="steering-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
			<path d="M0,0 L8,4 L0,8 z" class="fill-foreground" />
		</marker>
		<marker id="steering-shift" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
			<path d="M0,0 L8,4 L0,8 z" class="fill-muted-foreground" />
		</marker>
	</defs>

	{#each positive as [x, y], index (index)}
		<circle cx={x} cy={y} r="4.5" class="fill-lab-positive" fill-opacity="0.7" />
	{/each}
	{#each negative as [x, y], index (index)}
		<circle cx={x} cy={y} r="4.5" class="fill-lab-negative" fill-opacity="0.7" />
	{/each}

	<line x1={nx} y1={ny} x2={px} y2={py} class="stroke-foreground" stroke-width="2" marker-end="url(#steering-arrow)" />
	{#each [[px, py], [nx, ny]] as [x, y], index (index)}
		<path d="M{x - 6},{y - 6} L{x + 6},{y + 6} M{x - 6},{y + 6} L{x + 6},{y - 6}" class="stroke-foreground" stroke-width="2" />
	{/each}
	<text x={px + 12} y={py - 30} class="fill-lab-positive text-[11px] font-medium">positive prompts</text>
	<text x={nx - 40} y={ny + 56} class="fill-lab-negative text-[11px] font-medium">negative prompts</text>
	<text x={(px + nx) / 2 + 10} y={(py + ny) / 2 + 2} class="fill-foreground font-mono text-[11px]">v = μ₊ − μ₋</text>

	<circle cx={probe.x} cy={probe.y + 70} r="5" class="fill-card stroke-foreground" />
	<line
		x1={probe.x}
		y1={probe.y + 70}
		x2={probe.x + alpha * (px - nx)}
		y2={probe.y + 70 + alpha * (py - ny)}
		class="stroke-muted-foreground"
		stroke-dasharray="4 3"
		marker-end="url(#steering-shift)"
	/>
	<text x={probe.x + 10} y={probe.y + 92} class="fill-muted-foreground text-[10px]">
		a new token, plus {alpha}·v
	</text>
</svg>
