<script lang="ts">
	// Two invented passages, scored per token, to show what each summary
	// number does and does not see.
	const passages = [
		{
			name: 'Dense notation',
			bits: [5.1, 6.2, 4.8, 5.9, 6.6, 5.3, 4.9, 6.1, 5.6, 5.0, 6.4, 5.2, 5.8, 4.7],
		},
		{
			name: 'Quote with one odd line',
			bits: [1.8, 2.4, 1.2, 2.0, 1.5, 2.6, 13.2, 11.1, 1.9, 1.4, 2.2, 1.6, 2.1, 1.7],
		},
	];
	const lane = { height: 64, bar: 18, maxBits: 14 };
	const mean = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / values.length;
</script>

<svg viewBox="0 0 640 196" class="h-auto w-full" role="img" aria-labelledby="meanpeak-title">
	<title id="meanpeak-title">Mean and peak surprisal for two passages</title>
	{#each passages as passage, row (passage.name)}
		{@const top = 24 + row * (lane.height + 34)}
		{@const base = top + lane.height}
		{@const average = mean(passage.bits)}
		{@const peak = Math.max(...passage.bits)}
		<text x="12" y={top - 8} class="fill-foreground text-[11px] font-medium">{passage.name}</text>
		<line x1="12" y1={base} x2={12 + passage.bits.length * lane.bar} y2={base} class="stroke-border-strong" />
		{#each passage.bits as bits, index (index)}
			<rect
				x={12 + index * lane.bar + 2}
				y={base - (bits / lane.maxBits) * lane.height}
				width={lane.bar - 4}
				height={(bits / lane.maxBits) * lane.height}
				rx="1.5"
				class={bits === peak ? 'fill-lab-negative' : 'fill-lab-positive'}
				fill-opacity={bits === peak ? 1 : 0.5}
			/>
		{/each}
		<line
			x1="12"
			y1={base - (average / lane.maxBits) * lane.height}
			x2={12 + passage.bits.length * lane.bar}
			y2={base - (average / lane.maxBits) * lane.height}
			class="stroke-foreground"
			stroke-dasharray="4 3"
		/>
		<text x="290" y={top + 22} class="fill-foreground font-mono text-[11px]">mean {average.toFixed(1)} bits</text>
		<text x="290" y={top + 40} class="fill-lab-negative font-mono text-[11px]">peak {peak.toFixed(1)} bits</text>
		<text x="420" y={top + 22} class="fill-muted-foreground text-[11px]">
			{row === 0 ? 'high mean: surprising throughout' : 'low mean, high peak:'}
		</text>
		<text x="420" y={top + 40} class="fill-muted-foreground text-[11px]">
			{row === 0 ? 'ranks high on mean' : 'one place to look'}
		</text>
	{/each}
</svg>
