<script lang="ts">
	import { ImagePlus } from '@lucide/svelte';
	import { goto } from '$app/navigation';
	import History from '#lib/components/studio/History.svelte';
	import ImageSettings from '#lib/components/studio/ImageSettings.svelte';
	import StudioLayout from '#lib/components/studio/StudioLayout.svelte';
	import type { StudioImage } from '#lib/api/types.ts';
	import { Generator } from '#lib/studio/generator.svelte.ts';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Built once from what the page loaded; a reload asks the services again.
	// svelte-ignore state_referenced_locally
	const generator = new Generator(data.offers, data.history);

	let settingsOpen = $state(false);

	function reuse(image: StudioImage) {
		// On a narrow screen the form is a sheet: open it, so the filled form is what you see.
		if (generator.reuse(image)) settingsOpen = window.matchMedia('(max-width: 1023px)').matches;
	}
</script>

<svelte:head>
	<title>Generate · Studio · Primer</title>
</svelte:head>

{#snippet settings()}
	<ImageSettings
		{generator}
		problem={data.providersProblem}
		none="No image endpoint here makes images from a prompt. An administrator can declare one."
		prompt={{ label: 'Prompt', placeholder: 'A lighthouse at dusk, in watercolour.' }}
		onsubmit={() => generator.run()}
	/>
{/snippet}

{#snippet side()}
	<History
		{generator}
		problem={data.historyProblem}
		targets={data.libraryTargets}
		onreuse={reuse}
		onedit={(image) => goto(`/studio/edit?source=${image.id}`)}
	/>
{/snippet}

<StudioLayout title="Generate" {generator} {settings} {side} bind:settingsOpen>
	{#if generator.results.length > 0}
		<ul
			class="grid gap-4 transition-opacity {generator.results.length > 1 ? 'sm:grid-cols-2' : ''}
				{generator.running ? 'opacity-60' : ''}"
		>
			{#each generator.results as image (image.id)}
				<li>
					<button
						type="button"
						onclick={() => (generator.selectedId = image.id)}
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
				Describe an image and pick what makes it. It is kept in your history, with the settings
				that made it.
			</p>
		</div>
	{/if}
</StudioLayout>
