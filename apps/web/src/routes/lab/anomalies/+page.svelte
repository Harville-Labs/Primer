<script lang="ts">
	import { Activity, Bookmark } from '@lucide/svelte';
	import { Button } from '@sivir-ui/svelte';
	import { onMount } from 'svelte';
	import ActivationHeatmap from '#lib/components/lab/ActivationHeatmap.svelte';
	import DetectorPicker from '#lib/components/lab/DetectorPicker.svelte';
	import ExperimentsSheet from '#lib/components/lab/ExperimentsSheet.svelte';
	import LabEmpty from '#lib/components/lab/LabEmpty.svelte';
	import LabShell from '#lib/components/lab/LabShell.svelte';
	import ProbeSheet from '#lib/components/lab/ProbeSheet.svelte';
	import ScoreSourcePane from '#lib/components/lab/ScoreSourcePane.svelte';
	import SurpriseDetail from '#lib/components/lab/SurpriseDetail.svelte';
	import type { AnomaliesRun, Experiment } from '#lib/api/types.ts';
	import { entropyBasis, pastThreshold, sourcesFrom, stripRows } from '#lib/lab/anomalies.ts';
	import { Experiments } from '#lib/lab/experiments.svelte.ts';
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
	const experiments = new Experiments('anomalies');

	let probesOpen = $state(false);
	let savedOpen = $state(false);
	// Which row of the strip the selection is on; the token is the scorer's.
	let row = $state(0);

	let numLayers = $derived(data.status?.model?.num_layers ?? 0);

	// Null when Lab has no database, and saving is not offered at all.
	let retentionDays = $derived(data.status?.experiment_retention_days ?? null);
	let saveable = $derived<AnomaliesRun | null>(
		scorer.scored?.lab ? { kind: 'anomalies', ...scorer.scored.lab } : null
	);

	onMount(() => {
		if (data.status?.model) void probes.load();
		if (data.status?.model && retentionDays !== null) void experiments.load();
	});

	function reopen(experiment: Experiment) {
		if (experiment.run.kind !== 'anomalies' || !experiment.run.result) return;
		if (scorer.show(experiment.run.request, experiment.run.result)) {
			// Read again: the restore put the run's probe back under its id.
			void probes.load();
		}
	}

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

{#snippet actions()}
	{#if retentionDays !== null && data.status?.model}
		<Button
			variant="ghost"
			size="icon"
			onclick={() => (savedOpen = true)}
			title="Save this score, or open one saved before"
		>
			<Bookmark size={15} aria-hidden="true" />
			<span class="sr-only">Saved experiments</span>
		</Button>
	{/if}
{/snippet}

{#snippet scoreStatus()}
	{#if scored && score}
		<span class="tabular-nums">{score.tokens.length} tokens scored · {scored.source.label}</span>

		<div class="ml-auto flex items-center gap-4">
			<dl class="flex items-baseline gap-2">
				<dt>Perplexity</dt>
				<dd class="tabular-nums text-foreground" title="Over the scored tokens">
					{score.perplexity.toFixed(2)}
				</dd>
				{#if past !== null}
					<dt class="ml-2">Past threshold</dt>
					<dd
						class="tabular-nums {past > 0 ? 'text-warning' : 'text-foreground'}"
						title="Tokens whose distance from ordinary text passed the threshold"
					>
						{past}
					</dd>
				{/if}
			</dl>
			<!-- Drawn with the cells' own colour function, so it cannot disagree with them. -->
			<div
				class="hidden items-center gap-2 text-[10px] sm:flex"
				title="Each token's surprisal as a share of this text's most surprising token."
			>
				<span>0</span>
				<span
					aria-hidden="true"
					class="h-2 w-24 rounded-full border border-border"
					style:background-image="linear-gradient(90deg, var(--color-card), var(--color-lab-positive))"
				></span>
				<span>max</span>
			</div>
		</div>
	{/if}
{/snippet}

<!--
  For evaluating a model: where its answers - or a text it is shown - went
  somewhere it did not expect, token by token.
-->
<LabShell
	tool="anomalies"
	label="Surprisal"
	leftLabel="Source and text"
	left={sourcePane}
	rightLabel="Selected token and spans"
	right={detailPane}
	{actions}
	status={score ? scoreStatus : undefined}
>
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
			<LabEmpty icon={Activity} title="Find where a model was surprised" about="/lab/about/anomalies">
				Have a model answer a prompt, or show it a text, and see how unlikely it found each token.
				Spikes and stretches of uncertainty are flagged beside the strip.
			</LabEmpty>
		{/if}
	</div>
</LabShell>

{#if numLayers > 0}
	<ProbeSheet bind:open={probesOpen} {probes} {scorer} {numLayers} />
{/if}

{#if retentionDays !== null}
	<ExperimentsSheet
		bind:open={savedOpen}
		{experiments}
		{retentionDays}
		run={saveable}
		unsaved="Score with Lab first. A score from a chat endpoint cannot be saved here: Lab keeps only what its own model made."
		suggested={scorer.scored?.lab?.request.prompt ?? ''}
		onopen={reopen}
	/>
{/if}
