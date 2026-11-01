<script lang="ts">
	import { TriangleAlert } from '@lucide/svelte';
	import { Composer, Select, Spinner, ToggleGroup } from '@sivir-ui/svelte';
	import type { Scorer } from '#lib/lab/scorer.svelte.ts';

	/** Which source, generate or score, the text, and what happened last time. */
	interface Props {
		scorer: Scorer;
		//: Why a service gave the page no sources, each in its own words.
		problems: { title: string; detail: string }[];
		//: Whether Chat offered models at all, so "none declare logprobs" is not said of none.
		endpointModels: number;
	}

	let { scorer, problems, endpointModels }: Props = $props();

	let source = $derived(scorer.source);
	let generating = $derived(scorer.effectiveMode === 'generate');
</script>

<div class="flex h-full min-h-0 flex-col gap-5 px-4 py-4">
	<section aria-labelledby="source-heading" class="flex flex-col gap-2">
		<h2
			id="source-heading"
			class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
		>
			Source
		</h2>
		{#if scorer.sources.length > 0}
			<Select.Root bind:value={scorer.sourceKey}>
				<Select.Trigger
					class="h-9 w-full font-mono text-xs"
					disabled={scorer.running}
					aria-label="Model to score with"
				>
					<!-- As elsewhere in Lab: Select.Value is blank until the menu opens. -->
					<span class="truncate">{source?.label ?? 'Pick a model'}</span>
				</Select.Trigger>
				<Select.Content>
					{#each scorer.sources as each (each.key)}
						<Select.Item value={each.key} class="font-mono text-xs">{each.label}</Select.Item>
					{/each}
				</Select.Content>
			</Select.Root>
			{#if source}
				<p class="text-[11px] leading-relaxed text-muted-foreground">
					{#if source.kind === 'lab'}
						Lab's resident model. Entropy is exact, over the whole vocabulary.
					{:else}
						Through Chat, from the top 20 logprobs the endpoint returns at each token. Entropy
						from those is a lower bound.
					{/if}
				</p>
			{/if}
		{:else}
			<p class="rounded-lg border border-dashed border-border p-3 text-xs text-muted-foreground">
				{#if endpointModels > 0}
					No model here declares that its endpoint returns logprobs. An administrator declares
					it on a provider in <a href="/settings" class="underline">Settings</a>.
				{:else}
					Nothing to score with.
				{/if}
			</p>
		{/if}
		{#each problems as problem (problem.title)}
			<div role="alert" class="flex gap-2 rounded-lg border border-border bg-card p-3 text-xs">
				<TriangleAlert size={14} aria-hidden="true" class="mt-px shrink-0 text-warning" />
				<p><span class="font-medium">{problem.title}.</span> {problem.detail}</p>
			</div>
		{/each}
	</section>

	<section aria-labelledby="text-heading" class="flex flex-col gap-3">
		<h2
			id="text-heading"
			class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
		>
			{generating ? 'Prompt' : 'Text'}
		</h2>
		<!--
		  Offered only where the source can do it: an endpoint without
		  prompt logprobs can only score what it writes itself.
		-->
		{#if source?.scoresText}
			<ToggleGroup.Root
				type="single"
				value={scorer.mode}
				onValueChange={(value) => {
					if (value === 'generate' || value === 'text') scorer.mode = value;
				}}
			>
				<ToggleGroup.Item value="generate" class="text-xs">Generate</ToggleGroup.Item>
				<ToggleGroup.Item value="text" class="text-xs">Score this text</ToggleGroup.Item>
			</ToggleGroup.Root>
		{/if}
		<Composer.Root
			bind:value={scorer.text}
			disabled={!source || scorer.running}
			onSubmit={() => scorer.run()}
		>
			<Composer.Input
				placeholder={generating
					? 'Explain why the sky is blue.'
					: 'Paste an answer to see where it surprised the model.'}
				aria-label={generating ? 'Prompt to answer and score' : 'Text to score'}
			/>
			<Composer.Toolbar>
				<Composer.Actions></Composer.Actions>
				<Composer.Submit />
			</Composer.Toolbar>
		</Composer.Root>
		<p class="text-[11px] leading-relaxed text-muted-foreground">
			{generating
				? 'The model answers, and each token of the answer is scored by how unlikely the model found it.'
				: 'Each token is scored by how unlikely the model found it after the text before it. The first token has nothing before it and is not scored.'}
		</p>
	</section>

	{#if scorer.running}
		<p role="status" class="flex items-center gap-2 text-xs text-muted-foreground">
			<Spinner size={12} aria-hidden="true" />
			{generating ? 'Answering and scoring…' : 'Scoring…'}
		</p>
	{/if}

	{#if scorer.failure}
		<div role="alert" class="rounded-lg border border-error/40 bg-error/5 p-3 text-xs">
			<p class="font-medium text-error">{scorer.failure.title}</p>
			<p class="mt-1 text-foreground">{scorer.failure.detail}</p>
			{#if scorer.scored}
				<p class="mt-1 text-muted-foreground">The strip still shows the last score.</p>
			{/if}
		</div>
	{/if}
</div>
