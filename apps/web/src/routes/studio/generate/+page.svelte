<script lang="ts">
	import { ImagePlus, PanelLeft, PanelRight, TriangleAlert } from '@lucide/svelte';
	import { Button, Progress, Sheet, Spinner } from '@sivir-ui/svelte';
	import GenerateSettings from '#lib/components/studio/GenerateSettings.svelte';
	import ImageDetail from '#lib/components/studio/ImageDetail.svelte';
	import type { StudioImage } from '#lib/api/types.ts';
	import { Generator } from '#lib/studio/generator.svelte.ts';
	import { offerOf } from '#lib/studio/offer.ts';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Built once from what the page loaded; a reload asks the services again.
	// svelte-ignore state_referenced_locally
	const generator = new Generator(data.offers, data.history);

	let settingsOpen = $state(false);
	let historyOpen = $state(false);
	let selectedId = $state<string | null>(null);

	let known = $derived(new Map(generator.history.map((each) => [each.id, each])));
	// The newest result until something else is picked, so a run's images are what you see.
	let selected = $derived(
		(selectedId ? known.get(selectedId) : undefined) ?? generator.results[0] ?? null
	);
	let progress = $derived(generator.progress);

	$effect(() => {
		// A new run's results take over the detail pane.
		if (generator.results.length > 0) selectedId = generator.results[0].id;
	});

	function select(image: StudioImage) {
		selectedId = image.id;
	}

	function reuse(image: StudioImage) {
		if (generator.reuse(image)) settingsOpen = window.matchMedia('(max-width: 1023px)').matches;
	}

	async function remove(image: StudioImage) {
		if (!window.confirm('Delete this image? It cannot be brought back.')) return;
		await generator.remove(image);
		if (selectedId === image.id) selectedId = null;
	}
</script>

<svelte:head>
	<title>Generate · Studio · Primer</title>
</svelte:head>

{#snippet settings()}
	<GenerateSettings {generator} problem={data.providersProblem} />
{/snippet}

{#snippet history()}
	<div class="flex flex-col">
		{#if selected}
			<ImageDetail
				image={selected}
				offers={generator.offers}
				{known}
				reusable={offerOf(generator.offers, selected) !== null}
				onreuse={() => reuse(selected)}
				ondelete={() => remove(selected)}
				onselect={select}
			/>
		{/if}
		<section aria-labelledby="history-heading" class="flex flex-col gap-2 border-t border-border px-4 py-4">
			<h2
				id="history-heading"
				class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
			>
				History
			</h2>
			{#if data.historyProblem}
				<p role="alert" class="text-xs text-muted-foreground">
					<span class="font-medium text-foreground">{data.historyProblem.title}.</span>
					{data.historyProblem.detail}
				</p>
			{:else if generator.history.length === 0}
				<p class="text-xs text-muted-foreground">Nothing yet. What you make is kept here.</p>
			{:else}
				<ul class="grid grid-cols-3 gap-2">
					{#each generator.history as image (image.id)}
						<li>
							<button
								type="button"
								onclick={() => select(image)}
								aria-current={image.id === selected?.id ? 'true' : undefined}
								class="block w-full rounded-md focus-visible:outline-none focus-visible:shadow-[var(--focus-ring)]"
							>
								<img
									src="/studio/images/{image.id}/content"
									alt={image.provenance?.prompt ?? 'An uploaded image'}
									loading="lazy"
									class="aspect-square w-full rounded-md border object-cover
										{image.id === selected?.id ? 'border-foreground' : 'border-border'}"
								/>
							</button>
						</li>
					{/each}
				</ul>
				{#if generator.more}
					<Button variant="ghost" size="sm" onclick={() => generator.older()}>Older</Button>
				{/if}
			{/if}
		</section>
	</div>
{/snippet}

<!--
  The form on the left, what a run made in the middle, and on the right the
  picked image with how it was made above everything made before. Laid out
  like Lab's tabs, with the side panes as sheets on a narrow screen.
-->
<div
	class="flex h-full min-h-0 overflow-hidden rounded-xl border border-border bg-card
		shadow-[var(--elevation-float)]"
>
	<aside class="hidden w-80 shrink-0 overflow-y-auto border-r border-border bg-muted/30 lg:block">
		{@render settings()}
	</aside>

	<section aria-label="Results" class="flex min-w-0 flex-1 flex-col">
		<div class="flex items-center gap-3 border-b border-border px-4 py-2.5">
			<Button variant="ghost" size="icon" class="lg:hidden" onclick={() => (settingsOpen = true)}>
				<PanelLeft size={15} aria-hidden="true" />
				<span class="sr-only">Prompt and settings</span>
			</Button>
			<h1 class="text-sm font-semibold tracking-[-0.01em]">Generate</h1>
			{#if generator.running}
				<span role="status" class="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
					<Spinner size={12} aria-hidden="true" />
					{progress ? `${progress.value} of ${progress.max}` : 'Waiting for the image server'}
				</span>
			{/if}
			<Button variant="ghost" size="icon" class="ml-auto lg:hidden" onclick={() => (historyOpen = true)}>
				<PanelRight size={15} aria-hidden="true" />
				<span class="sr-only">Image and history</span>
			</Button>
		</div>

		{#if generator.running}
			{#if progress}
				<Progress value={progress.value} max={progress.max} class="h-[3px] rounded-none" />
			{:else}
				<Progress indeterminate class="h-[3px] rounded-none" />
			{/if}
		{/if}

		<div class="relative min-h-0 flex-1 overflow-auto overscroll-contain bg-card p-4">
			{#if generator.failure}
				<p role="alert" class="mb-4 flex gap-2 rounded-md border border-border px-3 py-2 text-xs">
					<TriangleAlert size={14} aria-hidden="true" class="mt-px shrink-0 text-warning" />
					<span>
						<span class="font-medium">{generator.failure.title}.</span>
						<span class="text-muted-foreground">{generator.failure.detail}</span>
					</span>
				</p>
			{/if}
			{#if generator.results.length > 0}
				<ul
					class="grid gap-4 transition-opacity {generator.results.length > 1 ? 'sm:grid-cols-2' : ''}
						{generator.running ? 'opacity-60' : ''}"
				>
					{#each generator.results as image (image.id)}
						<li>
							<button
								type="button"
								onclick={() => select(image)}
								class="block w-full rounded-lg focus-visible:outline-none focus-visible:shadow-[var(--focus-ring)]"
							>
								<img
									src="/studio/images/{image.id}/content"
									alt={image.provenance?.prompt ?? ''}
									width={image.width}
									height={image.height}
									class="max-h-[70vh] w-full rounded-lg border border-border bg-muted object-contain"
								/>
							</button>
						</li>
					{/each}
				</ul>
			{:else}
				<div class="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
					<span aria-hidden="true" class="grid size-12 place-items-center rounded-xl border border-border bg-muted">
						<ImagePlus size={18} class="text-muted-foreground" />
					</span>
					<p class="max-w-sm text-sm text-muted-foreground">
						Describe an image and pick what makes it. It is kept in your history, with the
						settings that made it.
					</p>
				</div>
			{/if}
		</div>
	</section>

	<aside class="hidden w-80 shrink-0 overflow-y-auto border-l border-border bg-muted/30 lg:block">
		{@render history()}
	</aside>
</div>

<Sheet.Root bind:open={settingsOpen}>
	<Sheet.Content side="left" class="w-80 overflow-y-auto p-0">
		{@render settings()}
	</Sheet.Content>
</Sheet.Root>

<Sheet.Root bind:open={historyOpen}>
	<Sheet.Content side="right" class="w-80 overflow-y-auto p-0">
		{@render history()}
	</Sheet.Content>
</Sheet.Root>
