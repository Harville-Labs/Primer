<script lang="ts">
	import { Grid3x3, PanelLeft, PanelRight, Rows3, Sparkles } from '@lucide/svelte';
	import { Button, Sheet, ToggleGroup } from '@sivir-ui/svelte';
	import { onMount } from 'svelte';
	import ActivationHeatmap from '#lib/components/lab/ActivationHeatmap.svelte';
	import ComparePane from '#lib/components/lab/ComparePane.svelte';
	import EditsPanel from '#lib/components/lab/EditsPanel.svelte';
	import LabPromptPane from '#lib/components/lab/LabPromptPane.svelte';
	import NeuronInspector from '#lib/components/lab/NeuronInspector.svelte';
	import SteerSheet from '#lib/components/lab/SteerSheet.svelte';
	import { Edits, Generator, Steering } from '#lib/lab/edits.svelte.ts';
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

	let steerOpen = $state(false);
	let pane = $state<'grid' | 'compare'>('grid');

	let promptOpen = $state(false);
	let detailOpen = $state(false);
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

	onMount(() => {
		if (data.status?.model) void steering.load();
	});

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

<!--
  Three panes wide on a desktop - the prompt, the grid, what the selected
  cell holds - because the grid is only worth reading with the other two in
  view. Below the breakpoint the grid keeps the screen and the other two
  come in from the edges, rather than stacking into a column where the grid
  is a thumbnail.
-->
<div
	class="flex h-full min-h-0 overflow-hidden rounded-xl border border-border bg-card
		shadow-[var(--elevation-float)]"
>
	<aside class="hidden w-72 shrink-0 overflow-y-auto border-r border-border bg-muted/30 lg:block">
		{@render promptPane()}
	</aside>

	<section aria-label="Activations" class="flex min-w-0 flex-1 flex-col">
		<div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-2.5">
			<Button
				variant="ghost"
				size="icon"
				class="lg:hidden"
				onclick={() => (promptOpen = true)}
			>
				<PanelLeft size={15} aria-hidden="true" />
				<span class="sr-only">Prompt and model</span>
			</Button>

			<div class="flex items-baseline gap-2">
				<h1 class="text-sm font-semibold tracking-[-0.01em]">Neurons</h1>
				{#if reading && pane === 'grid'}
					<span class="font-mono text-[11px] tabular-nums text-muted-foreground">
						{reading.layers.length} layers × {reading.tokens.length} tokens
					</span>
				{/if}
			</div>

			<ToggleGroup.Root
				type="single"
				value={pane}
				onValueChange={(value) => {
					if (value === 'grid' || value === 'compare') pane = value;
				}}
			>
				<ToggleGroup.Item value="grid" class="text-xs">Grid</ToggleGroup.Item>
				<ToggleGroup.Item value="compare" class="text-xs">Compare</ToggleGroup.Item>
			</ToggleGroup.Root>

			{#if pane === 'grid'}
			<ToggleGroup.Root
				type="single"
				value={inspector.signal}
				onValueChange={(value) => {
					if (typeof value === 'string') inspector.signal = value as Signal;
				}}
				class="ml-auto"
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
			<div class="hidden items-center gap-2 font-mono text-[10px] text-muted-foreground sm:flex"
				title={signal.hint}
			>
				<span>{signed ? '−' : '0'}</span>
				<span
					aria-hidden="true"
					class="h-2 w-28 rounded-full border border-border"
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
			{/if}

			<Button
				variant="ghost"
				size="icon"
				class="lg:hidden"
				onclick={() => (detailOpen = true)}
			>
				<PanelRight size={15} aria-hidden="true" />
				<span class="sr-only">Selected cell</span>
			</Button>
		</div>

		{#if pane === 'compare'}
			<div class="min-h-0 flex-1 bg-card">
				<ComparePane {generator} />
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
				<div class="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
					<span
						aria-hidden="true"
						class="grid size-12 place-items-center rounded-xl border border-border"
						style:background-image="linear-gradient(135deg, var(--color-card), color-mix(in oklab, var(--color-lab-positive) 35%, var(--color-card)))"
					>
						<Sparkles size={18} class="text-[var(--color-lab-positive)]" />
					</span>
					<p class="text-sm font-medium">Watch a prediction form</p>
					<p class="max-w-sm text-xs leading-relaxed text-muted-foreground">
						Run a prompt to see every layer's reading of every token: what the model would say
						next if it stopped there, how loud the residual stream is, and which neurons fire.
						Arrow keys move through the grid.
					</p>
				</div>
			{/if}
		</div>

		<div
			class="flex h-8 items-center gap-3 border-t border-border px-4 font-mono text-[11px]
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
	</section>

	<aside class="hidden w-80 shrink-0 overflow-y-auto border-l border-border bg-muted/30 lg:block">
		{@render detailPane()}
	</aside>
</div>

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

<Sheet.Root bind:open={promptOpen}>
	<Sheet.Content side="left" class="w-80 overflow-y-auto p-0">
		{@render promptPane()}
	</Sheet.Content>
</Sheet.Root>

<Sheet.Root bind:open={detailOpen}>
	<Sheet.Content side="right" class="w-80 overflow-y-auto p-0">
		{@render detailPane()}
	</Sheet.Content>
</Sheet.Root>
