<script lang="ts">
	import { BATCH, KEPT, LISTING } from '#lib/lab/scan.ts';

	const steps = [
		{ title: 'Library', lines: ['its passages,', `up to ${LISTING.toLocaleString('en')}`] },
		{ title: 'Chat lists', lines: ['Control checks', 'you can read it'] },
		{ title: `Batches of ${BATCH}`, lines: ['sent by the', 'web app'] },
		{ title: 'Lab scores', lines: ['one queue turn', 'per batch'] },
		{ title: 'Ranked', lines: [`top ${KEPT} kept,`, 'most unusual first'] },
	];
	const box = { width: 116, gap: 24, height: 64, y: 20 };
</script>

<svg viewBox="0 0 696 120" class="h-auto w-full" role="img" aria-labelledby="pipeline-title">
	<title id="pipeline-title">How a scan moves passages from a library to a ranked list</title>
	<defs>
		<marker id="pipeline-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
			<path d="M0,0 L8,4 L0,8 z" class="fill-muted-foreground" />
		</marker>
	</defs>
	{#each steps as step, index (step.title)}
		{@const x = 8 + index * (box.width + box.gap)}
		<rect
			{x}
			y={box.y}
			width={box.width}
			height={box.height}
			rx="8"
			class={index === 3 ? 'fill-card stroke-lab-positive' : 'fill-card stroke-border-strong'}
			stroke-width={index === 3 ? 1.5 : 1}
		/>
		<text x={x + box.width / 2} y={box.y + 22} text-anchor="middle" class="fill-foreground text-[12px] font-medium">
			{step.title}
		</text>
		{#each step.lines as line, row (row)}
			<text x={x + box.width / 2} y={box.y + 40 + row * 13} text-anchor="middle" class="fill-muted-foreground text-[10px]">
				{line}
			</text>
		{/each}
		{#if index < steps.length - 1}
			<line
				x1={x + box.width + 2}
				y1={box.y + box.height / 2}
				x2={x + box.width + box.gap - 2}
				y2={box.y + box.height / 2}
				class="stroke-muted-foreground"
				marker-end="url(#pipeline-arrow)"
			/>
		{/if}
	{/each}
	<path d="M 486 90 v 14 h -140 v -14" class="fill-none stroke-lab-positive" stroke-dasharray="3 3" marker-end="url(#pipeline-arrow)" />
	<text x="416" y="116" text-anchor="middle" class="fill-lab-positive text-[10px]">next batch, until the library is done</text>
</svg>
