<script lang="ts">
	// The shape of an attribution graph for a two-step question, after the
	// worked example in Ameisen et al. (2025). Nodes and weights are
	// illustrative: a real graph has hundreds of features, pruned to the
	// ones that carry most of the influence on the output.
	const nodes = {
		capital: { x: 90, y: 206, label: 'capital', kind: 'token' },
		state: { x: 210, y: 206, label: 'state', kind: 'token' },
		dallas: { x: 330, y: 206, label: 'Dallas', kind: 'token' },
		is: { x: 450, y: 206, label: 'is', kind: 'token' },
		texas: { x: 330, y: 130, label: 'Texas', kind: 'feature' },
		sayCapital: { x: 150, y: 130, label: 'say a capital', kind: 'feature' },
		sayAustin: { x: 270, y: 60, label: 'say Austin', kind: 'feature' },
		austin: { x: 450, y: 60, label: 'Austin', kind: 'output' },
	} as const;
	type Name = keyof typeof nodes;
	const edges: [Name, Name, number][] = [
		['dallas', 'texas', 3],
		['state', 'texas', 1.2],
		['capital', 'sayCapital', 2.6],
		['state', 'sayCapital', 1],
		['texas', 'sayAustin', 2.8],
		['sayCapital', 'sayAustin', 2.2],
		['sayAustin', 'austin', 3.2],
		['is', 'austin', 0.8],
		['texas', 'austin', 1],
	];
	const half = { token: 34, feature: 50, output: 34 };
</script>

<svg viewBox="0 0 540 236" class="mx-auto block h-auto w-full max-w-xl" role="img" aria-labelledby="graph-title">
	<title id="graph-title">An attribution graph from prompt tokens through features to the answer</title>
	<text x="12" y="210" class="fill-muted-foreground font-mono text-[10px]">tokens</text>
	<text x="12" y="134" class="fill-muted-foreground font-mono text-[10px]">features</text>
	<text x="12" y="64" class="fill-muted-foreground font-mono text-[10px]">logit</text>

	{#each edges as [from, to, weight] (`${from}-${to}`)}
		<line
			x1={nodes[from].x}
			y1={nodes[from].y - 12}
			x2={nodes[to].x}
			y2={nodes[to].y + 12}
			class="stroke-lab-positive"
			stroke-width={weight}
			stroke-opacity="0.55"
		/>
	{/each}

	{#each Object.entries(nodes) as [name, node] (name)}
		<rect
			x={node.x - half[node.kind]}
			y={node.y - 12}
			width={half[node.kind] * 2}
			height="24"
			rx={node.kind === 'token' ? 4 : 12}
			class={node.kind === 'output'
				? 'fill-lab-positive'
				: node.kind === 'feature'
					? 'fill-card stroke-lab-positive'
					: 'fill-secondary stroke-border-strong'}
		/>
		<text
			x={node.x}
			y={node.y + 4}
			text-anchor="middle"
			class="text-[11px] {node.kind === 'output' ? 'fill-on-lab font-medium' : 'fill-foreground'}"
		>
			{node.label}
		</text>
	{/each}
</svg>
