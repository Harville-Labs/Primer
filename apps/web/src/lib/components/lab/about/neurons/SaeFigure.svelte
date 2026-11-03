<script lang="ts">
	// A residual vector, the encoder's pre-activations, and what top-k leaves
	// of them. Invented values; a real SAE has tens of thousands of features
	// and keeps a few dozen.
	const residual = [0.6, -0.3, 0.9, 0.1, -0.7, 0.4, -0.2, 0.8, -0.5, 0.3, -0.9, 0.2];
	const pre = [0.1, 0.3, -0.2, 1.8, 0.2, -0.4, 0.4, 0.1, 1.2, -0.1, 0.3, 0.2, 2.1, -0.3, 0.1, 0.5, 0.2, -0.2];
	const k = 3;
	const top = new Set(
		pre
			.map((value, index) => [value, index] as const)
			.sort((a, b) => b[0] - a[0])
			.slice(0, k)
			.map(([, index]) => index),
	);
	const mid = 100;
</script>

<svg viewBox="0 0 640 190" class="h-auto w-full" role="img" aria-labelledby="sae-title">
	<title id="sae-title">A sparse autoencoder turning a dense residual vector into a few features</title>
	<defs>
		<marker id="sae-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
			<path d="M0,0 L8,4 L0,8 z" class="fill-muted-foreground" />
		</marker>
	</defs>

	{#each residual as value, index (index)}
		<rect
			x="20"
			y={34 + index * 11}
			width="28"
			height="10"
			class={value >= 0 ? 'fill-lab-positive' : 'fill-lab-negative'}
			fill-opacity={0.2 + 0.8 * Math.abs(value)}
		/>
	{/each}
	<text x="34" y="18" text-anchor="middle" class="fill-foreground text-[11px]">residual</text>
	<text x="34" y="182" text-anchor="middle" class="fill-muted-foreground text-[10px]">dense</text>

	<line x1="56" y1={mid} x2="104" y2={mid} class="stroke-muted-foreground" marker-end="url(#sae-arrow)" />
	<text x="80" y={mid - 8} text-anchor="middle" class="fill-muted-foreground font-mono text-[10px]">W·x + b</text>

	{#each [{ x0: 112, label: 'pre-activations', note: 'every feature', sparse: false }, { x0: 360, label: 'features', note: `top ${k} kept, rest zero`, sparse: true }] as panel (panel.label)}
		<line x1={panel.x0} y1={mid} x2={panel.x0 + pre.length * 11} y2={mid} class="stroke-border-strong" />
		{#each pre as value, index (index)}
			{@const shown = panel.sparse ? (top.has(index) ? value : 0) : value}
			<rect
				x={panel.x0 + index * 11 + 1}
				y={shown >= 0 ? mid - shown * 30 : mid}
				width="9"
				height={Math.max(1, Math.abs(shown) * 30)}
				class={top.has(index) ? 'fill-lab-positive' : 'fill-muted-foreground/40'}
			/>
		{/each}
		<text x={panel.x0 + (pre.length * 11) / 2} y="18" text-anchor="middle" class="fill-foreground text-[11px]">
			{panel.label}
		</text>
		<text x={panel.x0 + (pre.length * 11) / 2} y="182" text-anchor="middle" class="fill-muted-foreground text-[10px]">
			{panel.note}
		</text>
	{/each}

	<line x1="316" y1={mid} x2="352" y2={mid} class="stroke-muted-foreground" marker-end="url(#sae-arrow)" />
	<text x="334" y={mid - 8} text-anchor="middle" class="fill-muted-foreground font-mono text-[10px]">top-k</text>

	<line x1="564" y1={mid} x2="596" y2={mid} class="stroke-muted-foreground" marker-end="url(#sae-arrow)" />
	<text x="580" y={mid - 8} text-anchor="middle" class="fill-muted-foreground font-mono text-[10px]">decode</text>
	{#each residual as value, index (index)}
		<rect
			x="602"
			y={34 + index * 11}
			width="28"
			height="10"
			class={value >= 0 ? 'fill-lab-positive' : 'fill-lab-negative'}
			fill-opacity={0.15 + 0.7 * Math.abs(value)}
		/>
	{/each}
	<text x="616" y="18" text-anchor="middle" class="fill-foreground text-[11px]">rebuilt</text>
	<text x="616" y="182" text-anchor="middle" class="fill-muted-foreground text-[10px]">sum of 3</text>
</svg>
