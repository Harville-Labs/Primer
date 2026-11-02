<script lang="ts">
	import { Button } from '@sivir-ui/svelte';
	import type { StudioImage } from '#lib/api/types.ts';
	import ImageDetail from '#lib/components/studio/ImageDetail.svelte';
	import type { Problem } from '#lib/server/problem.ts';
	import type { Generator } from '#lib/studio/generator.svelte.ts';
	import { offerOf } from '#lib/studio/offer.ts';

	/** The picked image in detail, above everything this person has made. */
	interface Props {
		generator: Generator;
		//: Why Control gave the page no history, in its own words.
		problem: Problem | null;
		onreuse: (image: StudioImage) => void;
		onedit: (image: StudioImage) => void;
	}

	let { generator, problem, onreuse, onedit }: Props = $props();

	let selected = $derived(generator.selected);

	async function remove(image: StudioImage) {
		if (!window.confirm('Delete this image? It cannot be brought back.')) return;
		await generator.remove(image);
	}
</script>

<div class="flex flex-col">
	{#if selected}
		{@const image = selected}
		<ImageDetail
			{image}
			offers={generator.offers}
			known={generator.known}
			reusable={offerOf(generator.offers, image) !== null}
			onreuse={() => onreuse(image)}
			onedit={() => onedit(image)}
			ondelete={() => remove(image)}
			onselect={(each) => (generator.selectedId = each.id)}
		/>
	{/if}
	<section aria-labelledby="history-heading" class="flex flex-col gap-2 border-t border-border px-4 py-4">
		<h2
			id="history-heading"
			class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
		>
			History
		</h2>
		{#if problem}
			<p role="alert" class="text-xs text-muted-foreground">
				<span class="font-medium text-foreground">{problem.title}.</span>
				{problem.detail}
			</p>
		{:else if generator.history.length === 0}
			<p class="text-xs text-muted-foreground">Nothing yet. What you make is kept here.</p>
		{:else}
			<ul class="grid grid-cols-3 gap-2">
				{#each generator.history as image (image.id)}
					<li>
						<button
							type="button"
							onclick={() => (generator.selectedId = image.id)}
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
