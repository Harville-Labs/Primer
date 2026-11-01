<script lang="ts">
	import { cellColour } from '#lib/lab/heatmap.ts';

	/** Signed readings as bars drawn out from a centre line, strongest first. */
	interface Props {
		rows: { index: number; activation: number }[];
		//: Printed before each index: `n` for a neuron, `f` for a feature.
		prefix: string;
	}

	let { rows, prefix }: Props = $props();

	let peak = $derived(Math.max(1e-9, ...rows.map((row) => Math.abs(row.activation))));
	const number = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 3 });
</script>

<ol class="flex flex-col gap-px">
	{#each rows as row (row.index)}
		{@const share = (Math.abs(row.activation) / peak) * 50}
		<li class="grid grid-cols-[3.5rem_1fr_3.5rem] items-center gap-2 py-0.5">
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
		</li>
	{/each}
</ol>
