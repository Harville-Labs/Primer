<script lang="ts">
	import { Activity, PanelLeft, PanelRight } from '@lucide/svelte';
	import { Button, Sheet } from '@sivir-ui/svelte';
	import { onMount } from 'svelte';
	import ActivationHeatmap from '#lib/components/lab/ActivationHeatmap.svelte';
	import DetectorPicker from '#lib/components/lab/DetectorPicker.svelte';
	import ProbeSheet from '#lib/components/lab/ProbeSheet.svelte';
	import ScoreSourcePane from '#lib/components/lab/ScoreSourcePane.svelte';
	import SurpriseDetail from '#lib/components/lab/SurpriseDetail.svelte';
	import { entropyBasis, pastThreshold, sourcesFrom, stripRows } from '#lib/lab/anomalies.ts';
	import { Probes, Scorer } from '#lib/lab/scorer.svelte.ts';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Built once from what the page loaded: the sources are what this
	// deployment offered when the page opened, and a reload asks again.
	// svelte-ignore state_referenced_locally
	const scorer = new Scorer(sourcesFrom(data.models, data.status?.model ?? null), {
		labMaxNewTokens: data.status?.max_new_tokens,
	});

	const probes = new Probes();

	let sourceOpen = $state(false);
	let detailOpen = $state(false);
	let probesOpen = $state(false);
	// Which row of the strip the selection is on; the token is the scorer's.
	let row = $state(0);

	let numLayers = $derived(data.status?.model?.num_layers ?? 0);

	onMount(() => {
		if (data.status?.model) void probes.load();
	});

	let problems = $derived(
		[data.chatProblem, data.labProblem].filter((problem) => problem !== null)
	);
	let scored = $derived(scorer.scored);
	let score = $derived(scored?.score ?? null);
	let tokens = $derived(
		score?.tokens.map((token) => ({ id: token.index, text: token.token, special: false })) ?? []
	);
	let strip = $derived(scored ? stripRows(scored.score, scored.outliers, scored.probe) : []);
	let past = $derived(scored?.outliers ? pastThreshold(scored.outliers) : null);
</script>

<svelte:head>
	<title>Anomalies · Lab · Primer</title>
</svelte:head>

{#snippet sourcePane()}
	<ScoreSourcePane {scorer} {problems} endpointModels={data.models.length}>
		{#snippet detectors()}
			{#if scorer.source?.kind === 'lab' && numLayers > 0}
				<DetectorPicker {scorer} {probes} {numLayers} ontrain={() => (probesOpen = true)} />
			{/if}
		{/snippet}
	</ScoreSourcePane>
{/snippet}

{#snippet detailPane()}
	<SurpriseDetail {scorer} />
{/snippet}

<!--
  For evaluating a model: where its answers - or a text it is shown - went
  somewhere it did not expect, token by token. Laid out like Neurons, with
  the strip in the middle and the panes beside it, because the strip is only
  worth reading with the numbers in view.
-->
<div
	class="flex h-full min-h-0 overflow-hidden rounded-xl border border-border bg-card
		shadow-[var(--elevation-float)]"
>
	<aside class="hidden w-72 shrink-0 overflow-y-auto border-r border-border bg-muted/30 lg:block">
		{@render sourcePane()}
	</aside>

	<section aria-label="Surprisal" class="flex min-w-0 flex-1 flex-col">
		<div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-2.5">
			<Button variant="ghost" size="icon" class="lg:hidden" onclick={() => (sourceOpen = true)}>
				<PanelLeft size={15} aria-hidden="true" />
				<span class="sr-only">Source and text</span>
			</Button>

			<div class="flex items-baseline gap-2">
				<h1 class="text-sm font-semibold tracking-[-0.01em]">Anomalies</h1>
				{#if scored && score}
					<span class="font-mono text-[11px] tabular-nums text-muted-foreground">
						{score.tokens.length} tokens scored · {scored.source.label}
					</span>
				{/if}
			</div>

			{#if score}
				<dl class="ml-auto flex items-baseline gap-2 font-mono text-[11px] text-muted-foreground">
					<dt>Perplexity, over the scored tokens</dt>
					<dd class="tabular-nums text-foreground">{score.perplexity.toFixed(2)}</dd>
					{#if past !== null}
						<dt class="ml-2">Past the distance threshold</dt>
						<dd class="tabular-nums {past > 0 ? 'text-warning' : 'text-foreground'}">{past}</dd>
					{/if}
				</dl>
				<!-- Drawn with the cells' own colour function, so it cannot disagree with them. -->
				<div
					class="hidden items-center gap-2 font-mono text-[10px] text-muted-foreground sm:flex"
					title="Each token's surprisal as a share of this text's most surprising token."
				>
					<span>0</span>
					<span
						aria-hidden="true"
						class="h-2 w-28 rounded-full border border-border"
						style:background-image="linear-gradient(90deg, var(--color-card), var(--color-lab-positive))"
					></span>
					<span>max</span>
				</div>
			{/if}

			<Button
				variant="ghost"
				size="icon"
				class="lg:hidden {score ? '' : 'ml-auto'}"
				onclick={() => (detailOpen = true)}
			>
				<PanelRight size={15} aria-hidden="true" />
				<span class="sr-only">Selected token and spans</span>
			</Button>
		</div>

		<div class="relative min-h-0 flex-1 overflow-auto overscroll-contain bg-card">
			{#if scored && score}
				<div class="flex flex-col gap-4 p-4 transition-opacity {scorer.running ? 'opacity-60' : ''}">
					<ActivationHeatmap
						{tokens}
						values={strip.map((each) => each.values)}
						labels={strip.map((each) => each.labels)}
						labelsAre={strip.length > 1
							? 'bits, distance as a multiple of the threshold, or probe probability'
							: 'surprisal in bits'}
						dense={tokens.length > 48}
						rows={strip.map((each) => each.name)}
						corner={strip.length > 1 ? 'reading' : 'surprisal'}
						selected={{ layer: Math.min(row, strip.length - 1), token: scorer.selected }}
						label="Surprisal of each token in bits, coloured relative to the most surprising one, with any distance and probe readings above it."
						onselect={(cell) => {
							row = cell.layer;
							scorer.select(cell.token);
						}}
						onkey={(key) => scorer.key(key)}
					/>
					<p class="text-[11px] text-muted-foreground">
						{scored.mode === 'generate' ? 'The answer, as generated.' : 'The text, as given.'}
						Uncertainty is {entropyBasis(score)}.
					</p>
					<p class="max-w-prose whitespace-pre-wrap text-sm leading-relaxed">{scored.text}</p>
				</div>
			{:else}
				<div class="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
					<span
						aria-hidden="true"
						class="grid size-12 place-items-center rounded-xl border border-border"
						style:background-image="linear-gradient(135deg, var(--color-card), color-mix(in oklab, var(--color-lab-positive) 35%, var(--color-card)))"
					>
						<Activity size={18} class="text-[var(--color-lab-positive)]" />
					</span>
					<p class="text-sm font-medium">Find where a model was surprised</p>
					<p class="max-w-sm text-xs leading-relaxed text-muted-foreground">
						Have a model answer a prompt, or show it a text, and see how unlikely it found each
						token. Spikes and stretches of uncertainty are flagged beside the strip.
					</p>
				</div>
			{/if}
		</div>
	</section>

	<aside class="hidden w-80 shrink-0 overflow-y-auto border-l border-border bg-muted/30 lg:block">
		{@render detailPane()}
	</aside>
</div>

<Sheet.Root bind:open={sourceOpen}>
	<Sheet.Content side="left" class="w-80 overflow-y-auto p-0">
		{@render sourcePane()}
	</Sheet.Content>
</Sheet.Root>

{#if numLayers > 0}
	<ProbeSheet bind:open={probesOpen} {probes} {scorer} {numLayers} />
{/if}

<Sheet.Root bind:open={detailOpen}>
	<Sheet.Content side="right" class="w-80 overflow-y-auto p-0">
		{@render detailPane()}
	</Sheet.Content>
</Sheet.Root>
