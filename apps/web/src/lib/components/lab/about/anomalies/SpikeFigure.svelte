<script lang="ts">
	// Invented surprisal values. The median, spread, and threshold below
	// are computed from them exactly as Primer computes its own.
	const tokens = [
		[' The', 3.1], [' survey', 2.4], [' covers', 5.0], [' three', 1.2], [' cohorts', 4.1],
		[' across', 0.9], [' two', 2.8], [' IGNORE', 13.8], [' previous', 9.6], [' years', 2.2],
		[' and', 1.9], [' the', 0.6], [' same', 3.0], [' region', 2.6],
	] as const;

	function median(values: number[]): number {
		const sorted = [...values].sort((a, b) => a - b);
		const middle = sorted.length / 2;
		return sorted.length % 2 ? sorted[Math.floor(middle)] : (sorted[middle - 1] + sorted[middle]) / 2;
	}
	const bits = tokens.map(([, value]) => value);
	const centre = median(bits);
	const spread = 1.4826 * median(bits.map((value) => Math.abs(value - centre)));
	const threshold = centre + 3.5 * spread;

	const plot = { left: 44, top: 12, bottom: 160, maxBits: 15, step: 38 };
	const y = (value: number) => plot.bottom - (value / plot.maxBits) * (plot.bottom - plot.top);
</script>

<svg viewBox="0 0 640 230" class="h-auto w-full" role="img" aria-labelledby="spike-title">
	<title id="spike-title">Surprisal per token with the median and the spike threshold</title>
	{#each [0, 5, 10, 15] as value (value)}
		<text x={plot.left - 8} y={y(value) + 3} text-anchor="end" class="fill-muted-foreground font-mono text-[10px]">{value}</text>
	{/each}
	<rect
		x={plot.left + 7 * plot.step}
		y={plot.top - 4}
		width={plot.step * 2}
		height={plot.bottom - plot.top + 4}
		rx="4"
		class="fill-lab-negative"
		fill-opacity="0.1"
	/>
	{#each tokens as [token, value], index (index)}
		{@const spike = value >= threshold}
		<rect
			x={plot.left + index * plot.step + 7}
			y={y(value)}
			width={plot.step - 14}
			height={plot.bottom - y(value)}
			rx="2"
			class={spike ? 'fill-lab-negative' : 'fill-lab-positive'}
			fill-opacity={spike ? 1 : 0.55}
		/>
		<text
			x={plot.left + index * plot.step + plot.step / 2}
			y={plot.bottom + 12}
			text-anchor="end"
			transform="rotate(-40 {plot.left + index * plot.step + plot.step / 2} {plot.bottom + 12})"
			class="text-[10px] {spike ? 'fill-lab-negative font-medium' : 'fill-foreground'}"
		>
			{token.trim()}
		</text>
	{/each}
	<line x1={plot.left} y1={y(centre)} x2={plot.left + tokens.length * plot.step} y2={y(centre)} class="stroke-foreground" stroke-dasharray="4 3" />
	<text x={plot.left + tokens.length * plot.step + 4} y={y(centre) + 3} class="fill-foreground font-mono text-[10px]">median</text>
	<line x1={plot.left} y1={y(threshold)} x2={plot.left + tokens.length * plot.step} y2={y(threshold)} class="stroke-lab-negative" stroke-width="1.5" />
	<text x={plot.left + tokens.length * plot.step + 4} y={y(threshold) - 2} class="fill-lab-negative font-mono text-[10px]">z = 3.5</text>
	<text x={plot.left + tokens.length * plot.step + 4} y={y(threshold) + 10} class="fill-muted-foreground font-mono text-[9px]">{threshold.toFixed(1)} bits</text>
</svg>
