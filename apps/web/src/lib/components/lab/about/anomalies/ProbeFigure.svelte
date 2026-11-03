<script lang="ts">
	// Held-out examples placed by the probe's score, w·x + b. Invented
	// scores: two of twenty land on the wrong side, as they usually do.
	const positive = [2.9, 1.6, 3.4, 0.8, 2.2, 4.1, 1.1, -0.6, 2.6, 1.9];
	const negative = [-2.4, -1.3, -3.6, -0.4, -2.0, -2.9, 0.7, -1.7, -3.1, -1.0];
	const plot = { left: 40, right: 570, mid: 310, scale: 60, top: 24, bottom: 124 };
	const x = (score: number) => plot.mid + score * plot.scale;
	const sigmoid = (score: number) => 1 / (1 + Math.exp(-score));
	const curve = Array.from({ length: 61 }, (_, i) => -4.6 + (9.2 * i) / 60)
		.map((s, i) => `${i ? 'L' : 'M'}${x(s).toFixed(1)},${(plot.bottom - sigmoid(s) * (plot.bottom - plot.top)).toFixed(1)}`)
		.join(' ');
	const correct =
		positive.filter((score) => score > 0).length + negative.filter((score) => score <= 0).length;
	const total = positive.length + negative.length;
</script>

<svg viewBox="0 0 640 214" class="h-auto w-full" role="img" aria-labelledby="probe-title">
	<title id="probe-title">A probe's score separating two sets of examples</title>
	<line x1={plot.left} y1={plot.top} x2={plot.right} y2={plot.top} class="stroke-border" stroke-dasharray="2 4" />
	<line x1={plot.left} y1={plot.bottom} x2={plot.right} y2={plot.bottom} class="stroke-border-strong" />
	<text x={plot.left - 6} y={plot.top + 3} text-anchor="end" class="fill-muted-foreground font-mono text-[10px]">1</text>
	<text x={plot.left - 6} y={plot.bottom + 3} text-anchor="end" class="fill-muted-foreground font-mono text-[10px]">0</text>
	<path d={curve} class="fill-none stroke-foreground" stroke-width="1.5" />
	<text x={x(2.4)} y={plot.top - 8} class="fill-foreground font-mono text-[10px]">sigmoid(w·x + b)</text>
	<line x1={plot.mid} y1={plot.top - 12} x2={plot.mid} y2="186" class="stroke-muted-foreground" stroke-dasharray="4 3" />
	<text x={plot.mid + 6} y="196" class="fill-muted-foreground text-[10px]">decision boundary</text>

	{#each positive as score, index (index)}
		<circle cx={x(score)} cy="148" r="5" class="fill-lab-positive" fill-opacity="0.8" />
	{/each}
	{#each negative as score, index (index)}
		<circle cx={x(score)} cy="168" r="5" class="fill-lab-negative" fill-opacity="0.8" />
	{/each}
	<text x={plot.right + 4} y="152" class="fill-lab-positive text-[10px] font-medium">positive</text>
	<text x={plot.right + 4} y="172" class="fill-lab-negative text-[10px] font-medium">negative</text>

	<text x={plot.left} y="210" class="fill-foreground font-mono text-[11px]">
		held-out accuracy {correct}/{total} = {(correct / total).toFixed(2)}
	</text>
	<text x={plot.left + 230} y="210" class="fill-muted-foreground font-mono text-[11px]">
		chance 0.50 · needs more than {(0.5 + 2 * Math.sqrt(0.25 / total)).toFixed(2)}
	</text>
</svg>
