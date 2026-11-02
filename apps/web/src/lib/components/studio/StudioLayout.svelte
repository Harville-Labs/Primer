<script lang="ts">
	import { PanelLeft, PanelRight, TriangleAlert } from '@lucide/svelte';
	import { Button, Progress, Sheet, Spinner } from '@sivir-ui/svelte';
	import type { Snippet } from 'svelte';
	import type { Generator } from '#lib/studio/generator.svelte.ts';

	/**
	 * A Studio tab: the form on the left, the work in the middle, and on the
	 * right the picked image above everything made before. Laid out like
	 * Lab's tabs, with the side panes as sheets on a narrow screen.
	 */
	interface Props {
		title: string;
		generator: Generator;
		settings: Snippet;
		side: Snippet;
		children: Snippet;
		//: Bindable, so a tab can bring the form back after filling it.
		settingsOpen?: boolean;
	}

	let {
		title,
		generator,
		settings,
		side,
		children,
		settingsOpen = $bindable(false),
	}: Props = $props();

	let sideOpen = $state(false);
	let progress = $derived(generator.progress);
</script>

<div
	class="flex h-full min-h-0 overflow-hidden rounded-xl border border-border bg-card
		shadow-[var(--elevation-float)]"
>
	<aside class="hidden w-80 shrink-0 overflow-y-auto border-r border-border bg-muted/30 lg:block">
		{@render settings()}
	</aside>

	<section aria-label={title} class="flex min-w-0 flex-1 flex-col">
		<div class="flex items-center gap-3 border-b border-border px-4 py-2.5">
			<Button variant="ghost" size="icon" class="lg:hidden" onclick={() => (settingsOpen = true)}>
				<PanelLeft size={15} aria-hidden="true" />
				<span class="sr-only">Prompt and settings</span>
			</Button>
			<h1 class="text-sm font-semibold tracking-[-0.01em]">{title}</h1>
			{#if generator.running}
				<span role="status" class="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
					<Spinner size={12} aria-hidden="true" />
					{progress ? `${progress.value} of ${progress.max}` : 'Waiting for the image server'}
				</span>
			{/if}
			<Button variant="ghost" size="icon" class="ml-auto lg:hidden" onclick={() => (sideOpen = true)}>
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
			{@render children()}
		</div>
	</section>

	<aside class="hidden w-80 shrink-0 overflow-y-auto border-l border-border bg-muted/30 lg:block">
		{@render side()}
	</aside>
</div>

<Sheet.Root bind:open={settingsOpen}>
	<Sheet.Content side="left" class="w-80 overflow-y-auto p-0">
		{@render settings()}
	</Sheet.Content>
</Sheet.Root>

<Sheet.Root bind:open={sideOpen}>
	<Sheet.Content side="right" class="w-80 overflow-y-auto p-0">
		{@render side()}
	</Sheet.Content>
</Sheet.Root>
