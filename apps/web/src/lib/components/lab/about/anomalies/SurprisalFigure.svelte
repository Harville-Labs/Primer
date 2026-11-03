<script lang="ts">
	const plot = { left: 52, right: 452, top: 16, bottom: 196, maxBits: 10 };
	const x = (p: number) => plot.left + p * (plot.right - plot.left);
	const y = (bits: number) => plot.bottom - (bits / plot.maxBits) * (plot.bottom - plot.top);

	// Sampled densely near zero, where the curve is steep, and sparsely
	// elsewhere, so the path is smooth without hundreds of points.
	const curve = Array.from({ length: 80 }, (_, i) => 2 ** (-10 + (10 * i) / 79))
		.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p).toFixed(1)},${y(-Math.log2(p)).toFixed(1)}`)
		.join(' ');
	const marks = [
		{ p: 0.5, label: 'a coin flip: 1 bit' },
		{ p: 0.125, label: '1 in 8: 3 bits' },
		{ p: 1 / 64, label: '1 in 64: 6 bits' },
	];
</script>

<svg viewBox="0 0 480 236" class="mx-auto block h-auto w-full max-w-lg" role="img" aria-labelledby="surprisal-title">
	<title id="surprisal-title">Surprisal in bits against the probability the model gave a token</title>
	{#each [0, 2, 4, 6, 8, 10] as bits (bits)}
		<line x1={plot.left} y1={y(bits)} x2={plot.right} y2={y(bits)} class="stroke-border" stroke-dasharray={bits ? '2 4' : undefined} />
		<text x={plot.left - 8} y={y(bits) + 3} text-anchor="end" class="fill-muted-foreground font-mono text-[10px]">{bits}</text>
	{/each}
	{#each [0, 0.25, 0.5, 0.75, 1] as p (p)}
		<text x={x(p)} y={plot.bottom + 16} text-anchor="middle" class="fill-muted-foreground font-mono text-[10px]">{p}</text>
	{/each}
	<text x={(plot.left + plot.right) / 2} y="232" text-anchor="middle" class="fill-muted-foreground text-[10px]">
		probability the model gave the token that came next
	</text>
	<text x="14" y={(plot.top + plot.bottom) / 2} text-anchor="middle" transform="rotate(-90 14 {(plot.top + plot.bottom) / 2})" class="fill-muted-foreground text-[10px]">
		surprisal, bits
	</text>

	<path d={curve} class="fill-none stroke-lab-positive" stroke-width="2.5" />
	{#each marks as mark (mark.p)}
		<circle cx={x(mark.p)} cy={y(-Math.log2(mark.p))} r="4" class="fill-card stroke-lab-positive" stroke-width="2" />
		<text x={x(mark.p) + 10} y={y(-Math.log2(mark.p)) - 6} class="fill-foreground text-[11px]">{mark.label}</text>
	{/each}
</svg>
