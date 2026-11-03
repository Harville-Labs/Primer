<script lang="ts">
	// Illustrative: the lens readings are invented to show the shape a real
	// reading usually has - unsure early, settling on an answer late.
	const layers = [
		{ x: 70, name: 'Layer 1', guess: ' the', p: 0.12 },
		{ x: 240, name: 'Layer 2', guess: ' city', p: 0.31 },
		{ x: 410, name: 'Layer 3', guess: ' Paris', p: 0.84 },
	];
	const stream = 104;
</script>

<svg viewBox="0 0 640 214" class="h-auto w-full" role="img" aria-labelledby="residual-title">
	<title id="residual-title">
		The residual stream passing through three layers, with a logit-lens reading after each
	</title>
	<defs>
		<marker id="residual-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
			<path d="M0,0 L8,4 L0,8 z" class="fill-muted-foreground" />
		</marker>
	</defs>

	<rect x="4" y={stream - 14} width="52" height="28" rx="6" class="fill-secondary stroke-border-strong" />
	<text x="30" y={stream + 4} text-anchor="middle" class="fill-foreground text-[11px]">embed</text>
	<line x1="56" y1={stream} x2="568" y2={stream} class="stroke-foreground" stroke-width="3" marker-end="url(#residual-arrow)" />
	<rect x="572" y={stream - 14} width="64" height="28" rx="6" class="fill-secondary stroke-border-strong" />
	<text x="604" y={stream + 4} text-anchor="middle" class="fill-foreground text-[11px]">unembed</text>

	{#each layers as layer (layer.name)}
		<text x={layer.x + 52} y="14" text-anchor="middle" class="fill-muted-foreground font-mono text-[10px]">
			{layer.name}
		</text>
		{#each [{ dx: 0, label: 'attn' }, { dx: 58, label: 'MLP' }] as block (block.label)}
			<rect
				x={layer.x + block.dx}
				y="26"
				width="48"
				height="28"
				rx="6"
				class={block.label === 'MLP'
					? 'fill-card stroke-lab-positive'
					: 'fill-card stroke-border-strong'}
				stroke-width={block.label === 'MLP' ? 1.5 : 1}
			/>
			<text x={layer.x + block.dx + 24} y="44" text-anchor="middle" class="fill-foreground text-[11px]">
				{block.label}
			</text>
			<line x1={layer.x + block.dx + 24} y1="54" x2={layer.x + block.dx + 24} y2={stream - 8} class="stroke-muted-foreground" />
			<circle cx={layer.x + block.dx + 24} cy={stream} r="7" class="fill-card stroke-foreground" />
			<text x={layer.x + block.dx + 24} y={stream + 4} text-anchor="middle" class="fill-foreground text-[11px]">+</text>
		{/each}

		<line x1={layer.x + 118} y1={stream} x2={layer.x + 118} y2="142" class="stroke-lab-positive" stroke-dasharray="3 3" />
		<circle cx={layer.x + 118} cy={stream} r="3" class="fill-lab-positive" />
		<rect x={layer.x + 70} y="142" width="96" height="48" rx="6" class="fill-muted stroke-border" />
		<text x={layer.x + 78} y="158" class="fill-muted-foreground font-mono text-[9px]">lens says</text>
		<text x={layer.x + 78} y="174" class="fill-foreground font-mono text-[12px]">"{layer.guess.trim()}"</text>
		<rect x={layer.x + 78} y="180" width="80" height="4" rx="2" class="fill-border" />
		<rect x={layer.x + 78} y="180" width={80 * layer.p} height="4" rx="2" class="fill-lab-positive" />
		<text x={layer.x + 158} y="174" text-anchor="end" class="fill-muted-foreground font-mono text-[9px]">
			{Math.round(layer.p * 100)}%
		</text>
	{/each}

	<text x="320" y="208" text-anchor="middle" class="fill-muted-foreground text-[10px]">
		Prompt: "The capital of France is" - read at the last token
	</text>
</svg>
