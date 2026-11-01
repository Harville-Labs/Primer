<script lang="ts">
	import { tick } from 'svelte';
	import type { InspectedToken } from '#lib/api/types.ts';
	import { type Cell, cellColour, isDeep, visibleToken } from '#lib/lab/heatmap.ts';

	/**
	 * Layers up the side, tokens along the top, one coloured cell where they
	 * meet.
	 *
	 * Drawn as DOM cells rather than on a canvas, so each one is focusable,
	 * announced, and styled from the theme like everything else. Sivir has
	 * no grid of this kind, and a chart library would bring its own palette
	 * and its own idea of a pointer. Built for the Anomalies tab as well:
	 * nothing here knows what the values mean.
	 */
	interface Props {
		tokens: InspectedToken[];
		//: `[layer][token]`, each in -1..1. Negative is drawn in the negative ink.
		values: number[][];
		//: Text printed in each cell, `[layer][token]`; omitted when dense.
		labels?: string[][];
		//: What that text is, for a screen reader: `lens top "Paris"`.
		labelsAre?: string;
		selected: Cell;
		//: Narrow, textless cells, for a prompt too long to read cell by cell.
		dense?: boolean;
		//: What the grid is a reading of, for a screen reader's name for it.
		label: string;
		//: What each row is, printed beside it; layers `L0`, `L1`... when omitted.
		rows?: string[];
		//: What the rows are, printed above them.
		corner?: string;
		onselect: (cell: Cell) => void;
		//: Returns whether the key moved the selection, so others pass through.
		onkey: (key: string) => boolean;
		onpeek?: (cell: Cell | null) => void;
	}

	let {
		tokens,
		values,
		labels,
		labelsAre = 'lens top',
		selected,
		dense = false,
		label,
		rows,
		corner = 'layer',
		onselect,
		onkey,
		onpeek,
	}: Props = $props();

	let grid: HTMLDivElement | undefined = $state();

	// The last layer is drawn first, at the top, so a prediction can be
	// watched forming as the eye climbs toward the output.
	let order = $derived(values.map((_, layer) => values.length - 1 - layer));
	let columns = $derived(
		`3.25rem repeat(${tokens.length}, ${dense ? '0.875rem' : '4.25rem'})`
	);

	async function keydown(event: KeyboardEvent) {
		if (!onkey(event.key)) return;
		event.preventDefault();
		// Focus follows the selection, which is what makes this one tab
		// stop: the grid keeps a single focusable cell and moves it.
		await tick();
		grid
			?.querySelector<HTMLElement>(`[data-cell="${selected.layer}:${selected.token}"]`)
			?.focus();
	}
</script>

<!--
  One tab stop for the whole grid, with the arrow keys moving inside it - the
  pattern a grid is expected to follow, and the only one that stays usable
  with eighteen thousand cells.
-->
<div
	bind:this={grid}
	role="grid"
	aria-label={label}
	aria-rowcount={values.length + 1}
	aria-colcount={tokens.length + 1}
	tabindex="-1"
	onkeydown={keydown}
	onpointerleave={() => onpeek?.(null)}
	class="relative grid w-max select-none font-mono text-[10.5px] leading-none"
	style:grid-template-columns={columns}
>
	<div role="row" class="contents">
		<div
			role="columnheader"
			class="sticky left-0 top-0 z-30 flex items-end justify-end bg-card pb-2 pr-2
				text-[9px] uppercase tracking-[0.12em] text-muted-foreground"
		>
			{corner}
		</div>
		{#each tokens as token, index (index)}
			{@const active = index === selected.token}
			<div
				role="columnheader"
				title="{index}: {JSON.stringify(token.text)}{token.special ? ' (special)' : ''}"
				class="sticky top-0 z-20 flex h-14 items-end justify-center bg-card pb-1.5"
			>
				<span
					class="max-w-full truncate rounded-[3px] px-1 py-0.5 transition-colors duration-100
						motion-reduce:transition-none
						{dense ? '[writing-mode:vertical-rl] rotate-180 py-1' : ''}
						{active
						? 'bg-primary text-on-primary'
						: token.special
							? 'italic text-muted-foreground/70'
							: 'text-foreground'}"
				>
					{visibleToken(token.text)}
				</span>
			</div>
		{/each}
	</div>

	{#each order as layer (layer)}
		{@const row = values[layer]}
		{@const onRow = layer === selected.layer}
		<div role="row" class="contents">
			<div
				role="rowheader"
				class="sticky left-0 z-10 flex items-center justify-end bg-card pr-2 tabular-nums
					transition-colors duration-100 motion-reduce:transition-none
					{onRow ? 'font-semibold text-foreground' : 'text-muted-foreground'}"
			>
				{#if onRow}<span aria-hidden="true" class="mr-1 text-[8px]">▸</span>{/if}{rows?.[
					layer
				] ?? `L${layer}`}
			</div>
			{#each row as value, token (token)}
				{@const current = onRow && token === selected.token}
				{@const crossed = onRow || token === selected.token}
				<!--
				  The keys are handled once, on the grid, not on each cell:
				  a handler per cell is eighteen thousand of them doing what
				  one does, and the grid is where the roving focus lives.
				-->
				<!-- svelte-ignore a11y_click_events_have_key_events -->
				<div
					role="gridcell"
					data-cell="{layer}:{token}"
					tabindex={current ? 0 : -1}
					aria-selected={current}
					aria-label="{rows?.[layer] ?? `Layer ${layer}`}, token {token}{labels
						? `, ${labelsAre} ${JSON.stringify(labels[layer][token])}`
						: ''}"
					onclick={() => onselect({ layer, token })}
					onfocus={() => onselect({ layer, token })}
					onpointerenter={() => onpeek?.({ layer, token })}
					class="relative flex cursor-crosshair items-center justify-center overflow-hidden
						border-b border-r border-card focus-visible:outline-none
						{dense ? 'h-3.5' : 'h-6 px-1'}
						{current ? 'z-10 ring-2 ring-foreground ring-offset-1 ring-offset-card' : ''}
						{crossed && !current ? 'brightness-[1.04] contrast-[1.08]' : ''}"
					style:background-color={cellColour(value)}
					style:color={isDeep(value) ? 'var(--color-on-lab)' : 'var(--color-foreground)'}
				>
					{#if labels && !dense}
						<span class="truncate">{visibleToken(labels[layer][token])}</span>
					{/if}
				</div>
			{/each}
		</div>
	{/each}
</div>
