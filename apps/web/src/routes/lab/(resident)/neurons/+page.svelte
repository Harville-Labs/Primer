<script lang="ts">
	import { Bookmark, Grid3x3, Rows3, Sparkles } from '@lucide/svelte';
	import { Button, ToggleGroup } from '@sivir-ui/svelte';
	import { onMount } from 'svelte';
	import ActivationHeatmap from '#lib/components/lab/ActivationHeatmap.svelte';
	import ChatPane from '#lib/components/lab/ChatPane.svelte';
	import ComparePane from '#lib/components/lab/ComparePane.svelte';
	import EditsPanel from '#lib/components/lab/EditsPanel.svelte';
	import ExperimentsSheet from '#lib/components/lab/ExperimentsSheet.svelte';
	import GraphPane from '#lib/components/lab/GraphPane.svelte';
	import LabEmpty from '#lib/components/lab/LabEmpty.svelte';
	import LabPromptPane from '#lib/components/lab/LabPromptPane.svelte';
	import LabShell from '#lib/components/lab/LabShell.svelte';
	import NeuronInspector from '#lib/components/lab/NeuronInspector.svelte';
	import SteerSheet from '#lib/components/lab/SteerSheet.svelte';
	import type { Experiment, NeuronsRun } from '#lib/api/types.ts';
	import { SteeredChat } from '#lib/lab/chat.svelte.ts';
	import { Edits, Generator, Steering } from '#lib/lab/edits.svelte.ts';
	import { Experiments } from '#lib/lab/experiments.svelte.ts';
	import { Grapher } from '#lib/lab/grapher.svelte.ts';
	import {
		type Cell,
		cellValues,
		lensAt,
		SIGNALS,
		type Signal,
		topTokens,
		visibleToken,
	} from '#lib/lab/heatmap.ts';
	import { Inspector } from '#lib/lab/inspector.svelte.ts';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const inspector = new Inspector();
	const edits = new Edits();
	const generator = new Generator();
	const steering = new Steering();
	const experiments = new Experiments('neurons');
	const grapher = new Grapher();
	const chat = new SteeredChat();

	let steerOpen = $state(false);
	let savedOpen = $state(false);
	let pane = $state<'grid' | 'compare' | 'chat' | 'graph'>('grid');

	let promptOpen = $state(false);
	let peek = $state<Cell | null>(null);
	// Null until the reader picks, so the default can follow the prompt's
	// length: a long prompt is unreadable cell by cell, a short one is the
	// opposite.
	let denseChoice = $state<boolean | null>(null);

	let reading = $derived(inspector.reading);
	let values = $derived(reading ? cellValues(reading, inspector.signal) : []);
	let labels = $derived(reading ? topTokens(reading) : undefined);
	let dense = $derived(denseChoice ?? (reading?.tokens.length ?? 0) > 48);
	let signal = $derived(SIGNALS.find((each) => each.value === inspector.signal) ?? SIGNALS[0]);
	let signed = $derived(inspector.signal === 'neuron');
	let model = $derived(reading?.model ?? data.status?.model ?? null);
	let saes = $derived(model?.saes?.state === 'ready' ? model.saes : null);
	// Null when this Lab has no graph builder, and the Graph pane is not offered.
	let graphTokens = $derived(data.status?.graph_max_prompt_tokens ?? null);

	// Null when Lab has no database, and saving is not offered at all.
	let retentionDays = $derived(data.status?.experiment_retention_days ?? null);
	let saveable = $derived<NeuronsRun | null>(
		generator.asked && generator.view.completed
			? { kind: 'neurons', request: generator.asked, result: generator.view.completed }
			: null
	);

	onMount(() => {
		if (data.status?.model) void steering.load();
		if (data.status?.model && retentionDays !== null) void experiments.load();
	});

	/** Put a saved run back as it was: its prompt, its edits, and its two answers. */
	function reopen(experiment: Experiment) {
		if (experiment.run.kind !== 'neurons') return;
		const { request, result } = experiment.run;
		inspector.prompt = request.prompt;
		inspector.chat = request.chat;
		edits.replace(request.interventions ?? []);
		// Read again: the restore put the run's vectors back under their ids.
		void steering.load();
		if (result) {
			generator.show(request, result);
			pane = 'compare';
		}
	}

	// What the pointer is over, read out under the grid, so sweeping across a
	// row is a way of reading it without committing the selection.
	let peeked = $derived.by(() => {
		if (!reading || !peek) return null;
		const token = reading.tokens[peek.token];
		const top = lensAt(reading, peek)[0];
		if (!token || !top) return null;
		return { ...peek, token: visibleToken(token.text), top };
	});
</script>

<svelte:head>
	<title>Neurons · Lab · Primer</title>
</svelte:head>

{#snippet promptPane()}
	<LabPromptPane {inspector} status={data.status} problem={data.statusProblem}>
		{#if model && data.status}
			<EditsPanel
				{inspector}
				{edits}
				{generator}
				{steering}
				maxNewTokens={data.status.max_new_tokens}
				onsteer={() => (steerOpen = true)}
				ongenerate={() => {
					pane = 'compare';
					promptOpen = false;
				}}
			/>
		{/if}
	</LabPromptPane>
{/snippet}

{#snippet detailPane()}
	<!-- Zero first: an ablation is the question most edits start from. -->
	<NeuronInspector {inspector} onedit={(edit) => edits.add({ ...edit, op: 'zero' })} />
{/snippet}

{#snippet views()}
	<ToggleGroup.Root
		type="single"
		value={pane}
		onValueChange={(value) => {
			if (value === 'grid' || value === 'compare' || value === 'chat') pane = value;
			if (value === 'graph') {
				// Start from the prompt being read, which is usually the one to trace.
				if (!grapher.prompt) grapher.prompt = inspector.prompt;
				pane = value;
			}
		}}
	>
		<ToggleGroup.Item value="grid" class="text-xs">Grid</ToggleGroup.Item>
		<ToggleGroup.Item value="compare" class="text-xs">Compare</ToggleGroup.Item>
		{#if model && data.status}
			<ToggleGroup.Item value="chat" class="text-xs">Chat</ToggleGroup.Item>
		{/if}
		{#if graphTokens !== null && model}
			<ToggleGroup.Item value="graph" class="text-xs">Graph</ToggleGroup.Item>
		{/if}
	</ToggleGroup.Root>
{/snippet}

{#snippet actions()}
	{#if retentionDays !== null && model}
		<Button
			variant="ghost"
			size="icon"
			onclick={() => (savedOpen = true)}
			title="Save this run, or open one saved before"
		>
			<Bookmark size={15} aria-hidden="true" />
			<span class="sr-only">Saved experiments</span>
		</Button>
	{/if}
{/snippet}

{#snippet gridStatus()}
	<span class="tabular-nums">
		{#if reading}
			{reading.layers.length} layers × {reading.tokens.length} tokens
		{:else}
			No reading yet
		{/if}
	</span>

	<div class="ml-auto flex items-center gap-3">
		<ToggleGroup.Root
			type="single"
			value={inspector.signal}
			onValueChange={(value) => {
				if (typeof value === 'string') inspector.signal = value as Signal;
			}}
		>
			{#each SIGNALS as each (each.value)}
				<ToggleGroup.Item value={each.value} class="text-xs">
					{each.label}
				</ToggleGroup.Item>
			{/each}
		</ToggleGroup.Root>

		<!--
		  The scale, drawn with the same colour function as the cells, so
		  it cannot disagree with them in either theme.
		-->
		<div class="hidden items-center gap-2 text-[10px] sm:flex" title={signal.hint}>
			<span>{signed ? '−' : '0'}</span>
			<span
				aria-hidden="true"
				class="h-2 w-24 rounded-full border border-border"
				style:background-image={signed
					? 'linear-gradient(90deg, var(--color-lab-negative), var(--color-card), var(--color-lab-positive))'
					: 'linear-gradient(90deg, var(--color-card), var(--color-lab-positive))'}
			></span>
			<span>{signed ? '+' : '1'}</span>
		</div>

		<Button
			variant="ghost"
			size="icon"
			disabled={!reading}
			onclick={() => (denseChoice = !dense)}
			title={dense ? 'Show each cell’s top token' : 'Fit more tokens on screen'}
		>
			{#if dense}<Rows3 size={15} aria-hidden="true" />{:else}<Grid3x3
					size={15}
					aria-hidden="true"
				/>{/if}
			<span class="sr-only">{dense ? 'Show labels' : 'Compact cells'}</span>
		</Button>
	</div>
{/snippet}

<LabShell
	tool="neurons"
	label="Activations"
	leftLabel="Prompt and model"
	left={promptPane}
	rightLabel="Selected cell"
	right={detailPane}
	{views}
	{actions}
	status={pane === 'grid' ? gridStatus : undefined}
	bind:leftOpen={promptOpen}
>
	{#if pane === 'compare'}
		<div class="min-h-0 flex-1 bg-card">
			<ComparePane {generator} />
		</div>
	{:else if pane === 'chat' && model && data.status}
		<div class="min-h-0 flex-1 bg-card">
			<ChatPane {chat} {edits} {steering} maxNewTokens={data.status.max_new_tokens} />
		</div>
	{:else if pane === 'graph' && graphTokens !== null && model}
		<div class="min-h-0 flex-1 bg-card">
			<GraphPane
				{grapher}
				numLayers={model.num_layers}
				maxPromptTokens={graphTokens}
				transcoders={model.transcoders ?? null}
			/>
		</div>
	{:else}
		<div class="relative min-h-0 flex-1 overflow-auto overscroll-contain bg-card">
			{#if reading}
				<div class="p-4 transition-opacity {inspector.running ? 'opacity-60' : ''}">
					<ActivationHeatmap
						tokens={reading.tokens}
						{values}
						{labels}
						{dense}
						selected={inspector.selected}
						label="{signal.label} at each layer and token. {signal.hint}"
						onselect={(cell) => inspector.select(cell)}
						onkey={(key) => inspector.key(key)}
						onpeek={(cell) => (peek = cell)}
					/>
				</div>
			{:else}
				<LabEmpty icon={Sparkles} title="Watch a prediction form" about="/lab/about/neurons">
					Run a prompt to see every layer's reading of every token: what the model would say next
					if it stopped there, how loud the residual stream is, and which neurons fire. Arrow keys
					move through the grid.
				</LabEmpty>
			{/if}
		</div>

		<div
			class="flex h-8 shrink-0 items-center gap-3 border-t border-border px-4 font-mono text-[11px]
				text-muted-foreground"
			aria-live="off"
		>
			{#if peeked}
				<span class="tabular-nums">L{peeked.layer}</span>
				<span class="max-w-40 truncate text-foreground">{peeked.token}</span>
				<span aria-hidden="true">→</span>
				<span class="max-w-40 truncate text-foreground">{visibleToken(peeked.top.text)}</span>
				<span class="tabular-nums">{(peeked.top.probability * 100).toFixed(1)}%</span>
			{:else if reading}
				<span>Hover to peek · click or use the arrow keys to select</span>
			{/if}
		</div>
	{/if}
</LabShell>

{#if model}
	<SteerSheet
		bind:open={steerOpen}
		{edits}
		{steering}
		numLayers={model.num_layers}
		saeLayers={saes?.layers ?? []}
		saeWidth={saes?.width ?? null}
		chat={inspector.chat}
	/>
{/if}

{#if retentionDays !== null}
	<ExperimentsSheet
		bind:open={savedOpen}
		{experiments}
		{retentionDays}
		run={saveable}
		unsaved="Generate with edits first. A saved Neurons experiment is the prompt, its edits, and the two answers they produced."
		suggested={generator.asked?.prompt ?? ''}
		onopen={reopen}
	/>
{/if}
