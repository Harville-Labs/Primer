<script lang="ts">
	import { ImageUp, Pencil } from '@lucide/svelte';
	import { Button, ToggleGroup } from '@sivir-ui/svelte';
	import type { ImageOperation, StudioImage } from '#lib/api/types.ts';
	import History from '#lib/components/studio/History.svelte';
	import ImageSettings from '#lib/components/studio/ImageSettings.svelte';
	import MaskCanvas from '#lib/components/studio/MaskCanvas.svelte';
	import StudioLayout from '#lib/components/studio/StudioLayout.svelte';
	import { Generator } from '#lib/studio/generator.svelte.ts';
	import { encodeMask } from '#lib/studio/mask.ts';
	import { Painter } from '#lib/studio/painter.svelte.ts';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Built once from what the page loaded; a reload asks the services again.
	// svelte-ignore state_referenced_locally
	const generator = new Generator(data.offers, data.history);
	// svelte-ignore state_referenced_locally
	if (data.source) generator.edit(data.source);

	let settingsOpen = $state(false);
	let input = $state<HTMLInputElement>();

	const MODES: { value: ImageOperation; label: string }[] = [
		{ value: 'edit', label: 'Instruction' },
		{ value: 'inpaint', label: 'Painted mask' },
	];

	let source = $derived(generator.source);
	let modes = $derived(MODES.filter((mode) => generator.offer?.operations.includes(mode.value)));
	let inpainting = $derived(generator.operation === 'inpaint');
	// A mask belongs to one image: a new source starts a new one.
	let painter = $derived(source ? new Painter(source.width, source.height) : null);

	async function submit() {
		const mask = inpainting && painter && !painter.blank ? await encodeMask(painter.mask) : null;
		await generator.run(mask);
	}

	function reuse(image: StudioImage) {
		if (generator.reuse(image)) settingsOpen = window.matchMedia('(max-width: 1023px)').matches;
	}

	async function pickFile(event: Event & { currentTarget: HTMLInputElement }) {
		const file = event.currentTarget.files?.[0];
		event.currentTarget.value = '';
		if (file) await generator.upload(file);
	}
</script>

<svelte:head>
	<title>Edit · Studio · Primer</title>
</svelte:head>

{#snippet settings()}
	<ImageSettings
		{generator}
		problem={data.providersProblem}
		none="No image endpoint here edits images. An administrator can declare one."
		prompt={{
			label: 'Instruction',
			placeholder: inpainting ? 'A red front door.' : 'Make the sky stormy.',
		}}
		onsubmit={submit}
	>
		<section aria-labelledby="source-heading" class="flex flex-col gap-2">
			<h2
				id="source-heading"
				class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
			>
				Image
			</h2>
			{#if source}
				<img
					src="/studio/images/{source.id}/content"
					alt={source.provenance?.prompt ?? 'The image being edited'}
					class="aspect-video w-full rounded-md border border-border bg-muted object-contain"
				/>
			{:else}
				<p class="text-xs leading-relaxed text-muted-foreground">
					Pick one from your history, or add one of your own.
				</p>
			{/if}
			<input
				bind:this={input}
				type="file"
				accept="image/png,image/jpeg,image/webp"
				class="hidden"
				onchange={pickFile}
			/>
			<Button
				variant="secondary"
				size="sm"
				onclick={() => input?.click()}
				disabled={generator.running}
			>
				<ImageUp size={13} aria-hidden="true" />
				{source ? 'Use another' : 'Add an image'}
			</Button>
		</section>

		{#if modes.length > 1}
			<section aria-labelledby="mode-heading" class="flex flex-col gap-2">
				<h2
					id="mode-heading"
					class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
				>
					Change
				</h2>
				<ToggleGroup.Root
					type="single"
					value={generator.operation}
					onValueChange={(value) => {
						if (value) generator.operation = value as ImageOperation;
					}}
				>
					{#each modes as mode (mode.value)}
						<ToggleGroup.Item value={mode.value} disabled={generator.running} class="flex-1 text-xs">
							{mode.label}
						</ToggleGroup.Item>
					{/each}
				</ToggleGroup.Root>
			</section>
		{/if}
	</ImageSettings>
{/snippet}

{#snippet side()}
	<History
		{generator}
		problem={data.historyProblem}
		targets={data.libraryTargets}
		onreuse={reuse}
		onedit={(image) => generator.edit(image)}
	/>
{/snippet}

<StudioLayout title="Edit" {generator} {settings} {side} bind:settingsOpen>
	<div class="flex flex-col gap-6">
		{#if source && painter && inpainting}
			<MaskCanvas image={source} {painter} disabled={generator.running} />
		{:else if source}
			<img
				src="/studio/images/{source.id}/content"
				alt={source.provenance?.prompt ?? 'The image being edited'}
				width={source.width}
				height={source.height}
				class="mx-auto max-h-[65vh] w-auto rounded-lg border border-border bg-muted object-contain"
			/>
		{:else}
			<div class="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
				<span aria-hidden="true" class="grid size-12 place-items-center rounded-xl border border-border bg-muted">
					<Pencil size={18} class="text-muted-foreground" />
				</span>
				<p class="max-w-sm text-sm text-muted-foreground">
					Start from an image you made or one of your own, and say what to change. Paint a mask to
					keep the change to one part of it.
				</p>
			</div>
		{/if}

		{#if generator.results.length > 0}
			<section aria-labelledby="results-heading" class="flex flex-col gap-2">
				<h2
					id="results-heading"
					class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
				>
					Results
				</h2>
				<ul class="grid gap-4 sm:grid-cols-2 {generator.running ? 'opacity-60' : ''}">
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
									class="w-full rounded-lg border border-border bg-muted object-contain"
								/>
							</button>
						</li>
					{/each}
				</ul>
			</section>
		{/if}
	</div>
</StudioLayout>
