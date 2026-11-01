<script lang="ts">
	import ProbeVerdict from '#lib/components/lab/ProbeVerdict.svelte';
	import { pastThreshold } from '#lib/lab/anomalies.ts';
	import type { Scored } from '#lib/lab/scorer.svelte.ts';

	/** What Lab's detectors read at the selected token, and what to read them against. */
	let { scored, selected }: { scored: Scored; selected: number } = $props();

	let outliers = $derived(scored.outliers);
	let probe = $derived(scored.probe);
	let distance = $derived(outliers?.distances[selected] ?? null);
	let probability = $derived(probe?.probabilities[selected] ?? null);
</script>

{#if outliers && distance !== null}
	<section aria-labelledby="distance-heading" class="flex flex-col gap-2">
		<h2
			id="distance-heading"
			class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
		>
			Distance after L{outliers.layer}
		</h2>
		<dl class="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1.5 text-[11px]">
			<dt class="text-muted-foreground">This token, Mahalanobis</dt>
			<dd class="text-right font-mono tabular-nums">
				{distance.toFixed(1)}
				{#if distance > outliers.threshold}<span class="text-warning">· past</span>{/if}
			</dd>
			<dt class="text-muted-foreground">Threshold, 99th percentile of held-out reference</dt>
			<dd class="text-right font-mono tabular-nums">{outliers.threshold.toFixed(1)}</dd>
			<dt class="text-muted-foreground">Tokens past it</dt>
			<dd class="text-right font-mono tabular-nums">
				{pastThreshold(outliers)} of {outliers.distances.length}
			</dd>
		</dl>
		<p class="text-[10.5px] leading-relaxed text-muted-foreground">
			Measured against {outliers.reference_tokens} tokens of plain reference text. Ordinary text
			crosses the threshold about one token in a hundred.
		</p>
	</section>
{/if}

{#if probe && probability !== null}
	<section aria-labelledby="probe-heading" class="flex flex-col gap-2">
		<h2
			id="probe-heading"
			class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
		>
			Probe · {probe.probe.label}
		</h2>
		<dl class="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1.5 text-[11px]">
			<dt class="text-muted-foreground">Probability it reads as the first set</dt>
			<dd class="text-right font-mono tabular-nums">{(probability * 100).toPrecision(3)}%</dd>
		</dl>
		<ProbeVerdict probe={probe.probe} />
	</section>
{/if}
