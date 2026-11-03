<script lang="ts">
	// Two next-token distributions with invented probabilities, and their
	// entropy computed from them.
	const panels = [
		{
			context: 'The capital of France is',
			options: [[' Paris', 0.86], [' a', 0.06], [' the', 0.04], [' located', 0.02], [' not', 0.02]],
		},
		{
			context: 'My favourite colour is',
			options: [[' blue', 0.22], [' green', 0.2], [' red', 0.2], [' purple', 0.19], [' yellow', 0.19]],
		},
	] as const;
	const entropy = (options: readonly (readonly [string, number])[]) =>
		-options.reduce((sum, [, p]) => sum + p * Math.log2(p), 0);
</script>

<svg viewBox="0 0 640 196" class="h-auto w-full" role="img" aria-labelledby="entropy-title">
	<title id="entropy-title">A confident and an unsure next-token distribution, with their entropy</title>
	{#each panels as panel, column (panel.context)}
		{@const x0 = 20 + column * 320}
		<text x={x0} y="16" class="fill-muted-foreground text-[11px]">after "{panel.context}"</text>
		{#each panel.options as [token, p], row (token)}
			<text x={x0 + 64} y={40 + row * 24} text-anchor="end" class="fill-foreground font-mono text-[11px]">
				{token.trim()}
			</text>
			<rect x={x0 + 72} y={30 + row * 24} width={200 * p} height="14" rx="2" class="fill-lab-positive" fill-opacity="0.75" />
			<text x={x0 + 78 + 200 * p} y={41 + row * 24} class="fill-muted-foreground font-mono text-[10px]">{p}</text>
		{/each}
		<text x={x0} y="178" class="fill-foreground font-mono text-[12px] font-medium">
			H = {entropy(panel.options).toFixed(2)} bits
		</text>
		<text x={x0 + 110} y="178" class="fill-muted-foreground text-[11px]">
			{column === 0 ? 'one clear choice' : 'no clear choice'}
		</text>
	{/each}
</svg>
