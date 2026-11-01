<script lang="ts">
	import { Network, TriangleAlert } from '@lucide/svelte';
	import { Button, Input, Slider, Spinner, Textarea } from '@sivir-ui/svelte';
	import AttributionGraph from '#lib/components/lab/AttributionGraph.svelte';
	import type { TranscoderStatus } from '#lib/api/types.ts';
	import { type Neighbour, neighbours, nodeName, visible } from '#lib/lab/graph.ts';
	import type { Grapher } from '#lib/lab/grapher.svelte.ts';

	/** Trace which features carried a prompt to a prediction, and read the trace. */
	interface Props {
		grapher: Grapher;
		numLayers: number;
		maxPromptTokens: number;
		/** Null when this model has no transcoders mapped to it. */
		transcoders: TranscoderStatus | null;
	}

	let { grapher, numLayers, maxPromptTokens, transcoders }: Props = $props();

	let ready = $derived(transcoders?.state === 'ready');
	let shown = $derived(grapher.built ? visible(grapher.built.graph, grapher.influence) : null);
	let node = $derived(shown?.nodes.find((each) => each.id === grapher.selected) ?? null);
	let around = $derived(shown && node ? neighbours(shown, node.id) : null);

	const plain = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
	const signed = new Intl.NumberFormat(undefined, {
		maximumFractionDigits: 2,
		signDisplay: 'exceptZero',
	});
</script>

{#snippet list(title: string, items: Neighbour[])}
	<div class="flex min-w-0 flex-col gap-1">
		<span class="text-[11px] text-muted-foreground">{title}</span>
		{#each items as { node: other, weight } (other.id)}
			<button
				type="button"
				class="flex items-center justify-between gap-3 rounded px-1.5 py-0.5 text-left font-mono text-[11px] hover:bg-muted"
				onclick={() => grapher.select(other.id)}
			>
				<span class="truncate">{nodeName(other)}</span>
				<span
					class="tabular-nums"
					style:color={weight < 0 ? 'var(--color-lab-negative)' : 'var(--color-lab-positive)'}
				>
					{signed.format(weight)}
				</span>
			</button>
		{:else}
			<span class="px-1.5 font-mono text-[11px] text-muted-foreground">none shown</span>
		{/each}
	</div>
{/snippet}

<div class="flex h-full min-h-0 flex-col">
	<form
		class="grid gap-3 border-b border-border p-4 md:grid-cols-[minmax(0,1fr)_12rem_14rem_auto] md:items-end"
		onsubmit={(event) => {
			event.preventDefault();
			void grapher.build();
		}}
	>
		<Textarea
			bind:value={grapher.prompt}
			label="Prompt"
			description="Read as plain text, without the chat template. At most {maxPromptTokens} tokens."
			placeholder="The capital of the state containing Dallas is"
			rows={2}
			autoresize
		/>
		<label class="flex flex-col gap-1 text-[11px] text-muted-foreground">
			Explain a token
			<Input bind:value={grapher.target} placeholder=" Austin" class="h-8 font-mono text-xs" />
			<span>Empty explains the likeliest ones.</span>
		</label>
		<Slider
			bind:value={grapher.nodeThreshold}
			min={0.1}
			max={1}
			step={0.05}
			label="Node threshold"
			format={(value) => `${Math.round(value * 100)}% of influence`}
		/>
		<Button type="submit" disabled={!ready || grapher.running || !grapher.prompt} class="gap-1.5">
			{#if grapher.running}<Spinner size={14} aria-hidden="true" />{:else}<Network size={14} aria-hidden="true" />{/if}
			Trace
		</Button>
	</form>

	{#if !ready}
		<p class="border-b border-border px-4 py-2 text-xs text-muted-foreground">
			{#if !transcoders}
				This model has no transcoders, so Lab cannot trace it. Switch to one the operator has
				mapped transcoders to.
			{:else if transcoders.state === 'failed'}
				The transcoders could not be used: {transcoders.error}
			{:else}
				The transcoders for this model are still downloading. Tracing opens when they are ready.
			{/if}
		</p>
	{/if}

	{#if grapher.failure}
		<div role="alert" class="m-4 mb-0 flex gap-2 rounded-lg border border-error/40 bg-error/5 p-3 text-xs">
			<TriangleAlert size={14} aria-hidden="true" class="mt-px shrink-0 text-error" />
			<p>
				<span class="font-medium text-error">{grapher.failure.title}.</span>
				{grapher.failure.detail}
			</p>
		</div>
	{/if}

	<div
		class="relative min-h-0 flex-1 overflow-auto overscroll-contain bg-card transition-opacity
			{grapher.running ? 'opacity-60' : ''}"
	>
		{#if shown}
			<div class="p-4">
				<AttributionGraph
					graph={shown}
					{numLayers}
					selected={grapher.selected}
					onselect={(id) => grapher.select(id)}
				/>
			</div>
		{:else}
			<div class="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
				<Network size={18} aria-hidden="true" class="text-[var(--color-lab-positive)]" />
				<p class="text-sm font-medium">Trace a prediction back to the prompt</p>
				<p class="max-w-sm text-xs leading-relaxed text-muted-foreground">
					A trace replaces each layer's MLP with sparse features and follows which of them moved
					the prediction, and through what. It holds the model while it runs, so other runs wait.
				</p>
			</div>
		{/if}
	</div>

	{#if grapher.built && shown}
		<div class="grid gap-4 border-t border-border p-4 md:grid-cols-[14rem_minmax(0,1fr)_minmax(0,1fr)]">
			<div class="flex flex-col gap-2">
				<Slider
					bind:value={grapher.influence}
					min={0}
					max={1}
					step={0.01}
					label="Features shown"
					format={(value) => `${Math.round(value * 100)}% of influence`}
				/>
				<span class="font-mono text-[11px] tabular-nums text-muted-foreground">
					{shown.nodes.length} nodes · {shown.edges.length} edges{#if grapher.built.graph.dropped_edges}
						· {grapher.built.graph.dropped_edges} weakest left out{/if}
				</span>
			</div>
			{#if node && around}
				<div class="flex flex-col gap-1 md:col-span-2">
					<p class="font-mono text-xs">
						<span class="font-medium">{nodeName(node)}</span>
						<span class="text-muted-foreground">
							at token {node.position}
							{#if node.activation != null}· activation {plain.format(node.activation)}{/if}
							{#if node.probability != null}· {(node.probability * 100).toFixed(1)}%{/if}
						</span>
					</p>
					<div class="grid gap-4 sm:grid-cols-2">
						{@render list('Moved by', around.from)}
						{@render list('Moves', around.to)}
					</div>
				</div>
			{:else}
				<p class="text-xs text-muted-foreground md:col-span-2">
					Pick a node to see what moved it and what it moved. Blue pushes towards, red away from.
				</p>
			{/if}
		</div>
	{/if}
</div>
