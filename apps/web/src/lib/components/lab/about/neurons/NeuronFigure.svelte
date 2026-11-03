<script lang="ts">
	// Twenty-four of an MLP's thousands of neurons, with invented values. The
	// three marked are the ones with the largest magnitude, whatever their
	// sign, which is what Lab keeps.
	const values = [
		0.2, -0.4, 0.1, 3.1, 0.3, -0.2, 0.5, -0.1, -2.6, 0.2, 0.4, -0.3, 0.1, 0.6, -0.2, 0.3, 2.2,
		-0.5, 0.1, 0.2, -0.3, 0.4, 0.1, -0.2,
	];
	const kept = new Set(
		values
			.map((value, index) => [Math.abs(value), index] as const)
			.sort((a, b) => b[0] - a[0])
			.slice(0, 3)
			.map(([, index]) => index),
	);
	const bars = { x0: 268, width: 8, mid: 92, scale: 14 };
</script>

<svg viewBox="0 0 640 176" class="h-auto w-full" role="img" aria-labelledby="neuron-title">
	<title id="neuron-title">Inside a gated MLP block, where Lab reads its neurons</title>
	<defs>
		<marker id="neuron-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
			<path d="M0,0 L8,4 L0,8 z" class="fill-muted-foreground" />
		</marker>
	</defs>

	<text x="34" y="96" text-anchor="middle" class="fill-foreground text-[11px]">residual</text>
	<line x1="62" y1="92" x2="84" y2="62" class="stroke-muted-foreground" marker-end="url(#neuron-arrow)" />
	<line x1="62" y1="92" x2="84" y2="122" class="stroke-muted-foreground" marker-end="url(#neuron-arrow)" />

	<rect x="88" y="46" width="64" height="28" rx="6" class="fill-card stroke-border-strong" />
	<text x="120" y="64" text-anchor="middle" class="fill-foreground font-mono text-[10px]">gate_proj</text>
	<rect x="88" y="110" width="64" height="28" rx="6" class="fill-card stroke-border-strong" />
	<text x="120" y="128" text-anchor="middle" class="fill-foreground font-mono text-[10px]">up_proj</text>

	<line x1="152" y1="60" x2="172" y2="60" class="stroke-muted-foreground" marker-end="url(#neuron-arrow)" />
	<rect x="176" y="46" width="40" height="28" rx="6" class="fill-card stroke-border-strong" />
	<text x="196" y="64" text-anchor="middle" class="fill-foreground font-mono text-[10px]">SiLU</text>
	<line x1="216" y1="62" x2="234" y2="86" class="stroke-muted-foreground" />
	<line x1="152" y1="124" x2="234" y2="98" class="stroke-muted-foreground" />
	<circle cx="240" cy="92" r="9" class="fill-card stroke-foreground" />
	<text x="240" y="96" text-anchor="middle" class="fill-foreground text-[12px]">×</text>
	<line x1="249" y1="92" x2="262" y2="92" class="stroke-muted-foreground" marker-end="url(#neuron-arrow)" />

	<rect x="262" y="38" width={values.length * bars.width + 10} height="108" rx="6" class="fill-muted/60 stroke-lab-positive" stroke-dasharray="4 3" />
	<line x1={bars.x0} y1={bars.mid} x2={bars.x0 + values.length * bars.width} y2={bars.mid} class="stroke-border-strong" />
	{#each values as value, index (index)}
		<rect
			x={bars.x0 + index * bars.width + 1}
			y={value >= 0 ? bars.mid - value * bars.scale : bars.mid}
			width={bars.width - 2}
			height={Math.max(1, Math.abs(value) * bars.scale)}
			class={kept.has(index) ? (value >= 0 ? 'fill-lab-positive' : 'fill-lab-negative') : 'fill-muted-foreground/40'}
		/>
	{/each}
	<text x={262 + (values.length * bars.width + 10) / 2} y="28" text-anchor="middle" class="fill-lab-positive text-[11px] font-medium">
		Lab reads here
	</text>
	<text x={262 + (values.length * bars.width + 10) / 2} y="164" text-anchor="middle" class="fill-muted-foreground text-[10px]">
		neuron activations - the strongest are kept
	</text>

	<line x1={272 + values.length * bars.width} y1="92" x2={292 + values.length * bars.width} y2="92" class="stroke-muted-foreground" marker-end="url(#neuron-arrow)" />
	<rect x={296 + values.length * bars.width} y="78" width="74" height="28" rx="6" class="fill-card stroke-border-strong" />
	<text x={333 + values.length * bars.width} y="96" text-anchor="middle" class="fill-foreground font-mono text-[10px]">down_proj</text>
	<line x1={370 + values.length * bars.width} y1="92" x2={392 + values.length * bars.width} y2="92" class="stroke-muted-foreground" marker-end="url(#neuron-arrow)" />
	<text x={400 + values.length * bars.width} y="96" class="fill-foreground text-[11px]">stream</text>
</svg>
