<script lang="ts">
	import type { BuiltGraph } from '#lib/api/types.ts';
	import { GUTTER, layout, nodeName, type Placed } from '#lib/lab/graph.ts';

	/**
	 * An attribution graph drawn in place: the prompt along the bottom, a row
	 * per layer, and the predictions on top. Plain SVG, because the layout
	 * is fixed by the model's own structure and needs no graph library to
	 * find it.
	 */
	interface Props {
		graph: BuiltGraph;
		numLayers: number;
		selected: string | null;
		onselect: (id: string | null) => void;
	}

	let { graph, numLayers, selected, onselect }: Props = $props();

	let placed = $derived(layout(graph, numLayers));
	// What the selection touches, so everything else can step back.
	let touching = $derived.by(() => {
		if (!selected) return null;
		const ids = new Set([selected]);
		for (const { edge } of placed.edges) {
			if (edge.source === selected) ids.add(edge.target);
			if (edge.target === selected) ids.add(edge.source);
		}
		return ids;
	});

	function path(from: Placed, to: Placed): string {
		const bend = (from.y - to.y) / 2;
		return `M${from.x},${from.y} C${from.x},${from.y - bend} ${to.x},${to.y + bend} ${to.x},${to.y}`;
	}

	const RADIUS = { feature: 5, error: 4.5, token: 6, logit: 6.5 } as const;
</script>

<svg
	width={placed.width}
	height={placed.height}
	viewBox="0 0 {placed.width} {placed.height}"
	role="group"
	aria-label="Attribution graph: {graph.nodes.length} nodes and {graph.edges.length} edges"
	class="block select-none"
>
	{#each placed.rows as row (row.label)}
		<line
			x1={GUTTER}
			x2={placed.width}
			y1={row.y}
			y2={row.y}
			class="stroke-border"
			stroke-dasharray="2 4"
		/>
		<text x={GUTTER - 6} y={row.y + 3} text-anchor="end" class="fill-muted-foreground font-mono text-[9px]">
			{row.label}
		</text>
	{/each}

	<g fill="none">
		{#each placed.edges as { edge, from, to } (`${edge.source}>${edge.target}`)}
			{@const lit = !selected || edge.source === selected || edge.target === selected}
			<path
				d={path(from, to)}
				stroke={edge.weight < 0 ? 'var(--color-lab-negative)' : 'var(--color-lab-positive)'}
				stroke-width={0.5 + 3.5 * (Math.abs(edge.weight) / (placed.strongest || 1))}
				stroke-opacity={lit ? 0.55 : 0.06}
			/>
		{/each}
	</g>

	{#each placed.nodes as { node, x, y } (node.id)}
		{@const dim = touching !== null && !touching.has(node.id)}
		{@const radius = RADIUS[node.kind]}
		<g
			role="button"
			tabindex="0"
			aria-pressed={selected === node.id}
			aria-label={nodeName(node)}
			class="cursor-pointer outline-none transition-opacity focus-visible:[&>*:first-child]:stroke-ring"
			opacity={dim ? 0.25 : 1}
			onclick={() => onselect(node.id)}
			onkeydown={(event) => {
				if (event.key === 'Enter' || event.key === ' ') {
					event.preventDefault();
					onselect(node.id);
				} else if (event.key === 'Escape') onselect(null);
			}}
		>
			{#if node.kind === 'error'}
				<!-- A diamond: what the transcoder missed, not a feature it found. -->
				<rect
					x={x - radius}
					y={y - radius}
					width={radius * 2}
					height={radius * 2}
					transform="rotate(45 {x} {y})"
					class="fill-muted stroke-muted-foreground"
					stroke-width={selected === node.id ? 2.5 : 1}
				/>
			{:else}
				<circle
					cx={x}
					cy={y}
					r={radius}
					class={node.kind === 'feature' ? 'fill-card' : 'fill-foreground'}
					stroke={node.kind === 'feature' ? 'var(--color-lab-positive)' : 'var(--color-foreground)'}
					stroke-width={selected === node.id ? 3 : 1.5}
				/>
			{/if}
			<title>{nodeName(node)}</title>
			{#if node.kind === 'logit'}
				<text {x} y={y - 11} text-anchor="middle" class="fill-foreground font-mono text-[10px]">
					{nodeName(node)}
				</text>
			{/if}
		</g>
	{/each}

	{#each placed.columns as column, position (position)}
		<text
			x={column.x}
			y={placed.height - 4}
			text-anchor="middle"
			class="fill-foreground font-mono text-[10px]"
		>
			{column.token.length > 10 ? `${column.token.slice(0, 9)}…` : column.token}
		</text>
	{/each}
</svg>
