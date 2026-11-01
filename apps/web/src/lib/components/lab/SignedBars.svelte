<script lang="ts">
	import { Plus } from '@lucide/svelte';
	import { cellColour } from '#lib/lab/heatmap.ts';

	/** Signed readings as bars drawn out from a centre line, strongest first. */
	interface Props {
		//: A row's label, when it has one, is printed under its bar.
		rows: { index: number; activation: number; label?: string }[];
		//: Printed before each index: `n` for a neuron, `f` for a feature.
		prefix: string;
		//: Given, each row can be added as an edit; `noun` names it for that.
		onadd?: (index: number) => void;
		noun?: string;
	}

	let { rows, prefix, onadd, noun = 'component' }: Props = $props();

	let peak = $derived(Math.max(1e-9, ...rows.map((row) => Math.abs(row.activation))));
	const number = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 3 });
</script>

<ol class="flex flex-col gap-px">
	{#each rows as row (row.index)}
		{@const share = (Math.abs(row.activation) / peak) * 50}
		<li
			class="group grid items-center gap-2 py-0.5 {onadd
				? 'grid-cols-[3.5rem_1fr_3.5rem_1.25rem]'
				: 'grid-cols-[3.5rem_1fr_3.5rem]'}"
		>
			<span class="font-mono text-[11px] tabular-nums text-muted-foreground">
				{prefix}{row.index}
			</span>
			<!-- From a centre line, so the sign is where the bar goes. -->
			<span class="relative h-2.5 rounded-[2px] bg-secondary">
				<span aria-hidden="true" class="absolute inset-y-0 left-1/2 w-px bg-border-strong"></span>
				<span
					aria-hidden="true"
					class="absolute inset-y-0 rounded-[2px]"
					style:left={row.activation < 0 ? `${50 - share}%` : '50%'}
					style:width="{share}%"
					style:background-color={cellColour(row.activation < 0 ? -0.9 : 0.9)}
				></span>
			</span>
			<span class="text-right font-mono text-[11px] tabular-nums">
				{number.format(row.activation)}
			</span>
			{#if onadd}
				<button
					type="button"
					class="grid size-5 place-items-center rounded text-muted-foreground opacity-60
						hover:bg-secondary hover:text-foreground hover:opacity-100 focus-visible:opacity-100
						group-hover:opacity-100"
					title="Edit {noun} {row.index}"
					onclick={() => onadd(row.index)}
				>
					<Plus size={12} aria-hidden="true" />
					<span class="sr-only">Edit {noun} {row.index}</span>
				</button>
			{/if}
			{#if row.label}
				<span
					class="col-start-2 col-end-[-1] -mt-0.5 truncate text-[11px] text-muted-foreground"
					title={row.label}
				>
					{row.label}
				</span>
			{/if}
		</li>
	{/each}
</ol>
