<script lang="ts">
	// Mean surprisal for forty invented passages from one library, with a
	// fixed seed. One was given an injected instruction.
	let seed = 5;
	const uniform = () => {
		seed = (seed * 16807) % 2147483647;
		return seed / 2147483647;
	};
	const passages = Array.from({ length: 40 }, () => ({ bits: 2.2 + uniform() * 1.6 + uniform() * 0.8, lane: uniform() }));
	const injected = { bits: 6.9, lane: 0.5 };
	const plot = { left: 40, right: 600, top: 30, height: 70, maxBits: 8 };
	const x = (bits: number) => plot.left + (bits / plot.maxBits) * (plot.right - plot.left);
	const y = (lane: number) => plot.top + 10 + lane * (plot.height - 20);
</script>

<svg viewBox="0 0 640 150" class="h-auto w-full" role="img" aria-labelledby="ranking-title">
	<title id="ranking-title">Passages placed by mean surprisal, with one standing apart</title>
	<line x1={plot.left} y1={plot.top + plot.height} x2={plot.right} y2={plot.top + plot.height} class="stroke-border-strong" />
	{#each [0, 2, 4, 6, 8] as bits (bits)}
		<line x1={x(bits)} y1={plot.top} x2={x(bits)} y2={plot.top + plot.height} class="stroke-border" stroke-dasharray="2 4" />
		<text x={x(bits)} y={plot.top + plot.height + 16} text-anchor="middle" class="fill-muted-foreground font-mono text-[10px]">{bits}</text>
	{/each}
	<text x={(plot.left + plot.right) / 2} y="138" text-anchor="middle" class="fill-muted-foreground text-[10px]">
		mean surprisal of the passage, bits per token
	</text>
	{#each passages as passage, index (index)}
		<circle cx={x(passage.bits)} cy={y(passage.lane)} r="4.5" class="fill-lab-positive" fill-opacity="0.5" />
	{/each}
	<circle cx={x(injected.bits)} cy={y(injected.lane)} r="6.5" class="fill-lab-negative" />
	<text x={x(injected.bits)} y={plot.top - 8} text-anchor="middle" class="fill-lab-negative text-[11px] font-medium">
		"Ignore all previous instructions…"
	</text>
	<text x={x(3.2)} y={plot.top - 8} text-anchor="middle" class="fill-muted-foreground text-[11px]">the rest of the library</text>
</svg>
