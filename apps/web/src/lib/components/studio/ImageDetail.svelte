<script lang="ts">
	import { Download, LibraryBig, Pencil, RotateCcw, Trash2 } from '@lucide/svelte';
	import { Badge, Button, Select } from '@sivir-ui/svelte';
	import type { DocumentSummary, LibrarySummary, StudioImage } from '#lib/api/types.ts';
	import { lineage, type Offer } from '#lib/studio/offer.ts';

	/** One image: how it was made, what it was made from, and what can be done with it. */
	interface Props {
		image: StudioImage;
		offers: Offer[];
		//: Every image loaded, for the lineage strip.
		known: Map<string, StudioImage>;
		//: False when what made it is no longer offered, so reusing would not make it again.
		reusable: boolean;
		onreuse: () => void;
		onedit: () => void;
		ondelete: () => void;
		onselect: (image: StudioImage) => void;
		//: Libraries a copy may go to. Empty hides the action.
		targets: LibrarySummary[];
		onaddtolibrary: (libraryId: string) => Promise<DocumentSummary | null>;
	}

	let {
		image,
		offers,
		known,
		reusable,
		onreuse,
		onedit,
		ondelete,
		onselect,
		targets,
		onaddtolibrary,
	}: Props = $props();

	let targetId = $state('');
	let adding = $state(false);
	//: Where this image was last added, by image, so picking another clears it.
	let added = $state<{ imageId: string; library: LibrarySummary } | null>(null);
	let target = $derived(targets.find((each) => each.id === targetId) ?? targets[0] ?? null);

	async function addToLibrary() {
		const library = target;
		if (!library || adding) return;
		adding = true;
		const document = await onaddtolibrary(library.id);
		adding = false;
		if (document) added = { imageId: image.id, library };
	}

	let made = $derived(image.provenance ?? null);
	let provider = $derived(
		made ? (offers.find((each) => each.providerId === made.provider_id)?.providerName ?? null) : null
	);
	let ancestors = $derived(lineage(image, known));
	let sizeKb = $derived(Math.max(1, Math.round(image.byte_size / 1024)));
</script>

<div class="flex flex-col gap-4 px-4 py-4">
	<img
		src="/studio/images/{image.id}/content"
		alt={made?.prompt ?? 'An uploaded image'}
		width={image.width}
		height={image.height}
		class="w-full rounded-md border border-border bg-muted object-contain"
	/>

	<div class="flex flex-wrap gap-2">
		<Button
			variant="secondary"
			size="sm"
			href="/studio/images/{image.id}/content"
			download="primer-{image.id}.png"
		>
			<Download size={13} aria-hidden="true" />
			Download
		</Button>
		<Button variant="secondary" size="sm" onclick={onedit}>
			<Pencil size={13} aria-hidden="true" />
			Edit
		</Button>
		{#if made}
			<Button
				variant="secondary"
				size="sm"
				onclick={onreuse}
				disabled={!reusable}
				title={reusable ? 'Fill the form with these settings' : 'What made this is no longer offered'}
			>
				<RotateCcw size={13} aria-hidden="true" />
				Reuse settings
			</Button>
		{/if}
		<Button variant="ghost" size="sm" onclick={ondelete} class="ml-auto">
			<Trash2 size={13} aria-hidden="true" />
			Delete
		</Button>
	</div>

	{#if targets.length > 0}
		<div class="flex items-center gap-2">
			<Select.Root bind:value={targetId}>
				<Select.Trigger class="h-8 min-w-0 flex-1 text-xs" aria-label="Library">
					<span class="truncate">{target?.name}</span>
				</Select.Trigger>
				<Select.Content>
					{#each targets as each (each.id)}
						<Select.Item value={each.id} class="text-xs">{each.name}</Select.Item>
					{/each}
				</Select.Content>
			</Select.Root>
			<Button variant="secondary" size="sm" onclick={addToLibrary} disabled={adding}>
				<LibraryBig size={13} aria-hidden="true" />
				Add to library
			</Button>
		</div>
		{#if added?.imageId === image.id}
			<p role="status" class="text-xs text-muted-foreground">
				A copy is in <a class="underline" href="/libraries/{added.library.id}">{added.library.name}</a>.
				It can be searched and cited once it has been read.
			</p>
		{/if}
	{/if}

	{#if made}
		<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
			<dt class="text-muted-foreground">Prompt</dt>
			<dd class="whitespace-pre-wrap">{made.prompt}</dd>
			{#if made.negative_prompt}
				<dt class="text-muted-foreground">Avoid</dt>
				<dd class="whitespace-pre-wrap">{made.negative_prompt}</dd>
			{/if}
			<dt class="text-muted-foreground">Model</dt>
			<dd>{made.model}{provider ? ` · ${provider}` : ''}</dd>
			<dt class="text-muted-foreground">Size</dt>
			<dd class="font-mono tabular-nums">{made.size}</dd>
			{#if made.seed !== null && made.seed !== undefined}
				<dt class="text-muted-foreground">Seed</dt>
				<dd class="font-mono tabular-nums">{made.seed}</dd>
			{/if}
			{#if made.steps}
				<dt class="text-muted-foreground">Steps</dt>
				<dd class="font-mono tabular-nums">{made.steps}</dd>
			{/if}
			<dt class="text-muted-foreground">Made</dt>
			<dd>{new Date(image.created_at).toLocaleString()} · {sizeKb} KB</dd>
		</dl>
	{:else}
		<p class="text-xs text-muted-foreground">
			<Badge variant="outline">upload</Badge>
			{image.width}×{image.height} · {sizeKb} KB
		</p>
	{/if}

	{#if ancestors.length > 0}
		<section aria-label="Made from" class="flex flex-col gap-2">
			<h3 class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
				Made from
			</h3>
			<ol class="flex gap-2 overflow-x-auto">
				{#each ancestors as ancestor (ancestor.id)}
					<li class="shrink-0">
						<button
							type="button"
							onclick={() => onselect(ancestor)}
							class="block rounded-md focus-visible:outline-none focus-visible:shadow-[var(--focus-ring)]"
						>
							<img
								src="/studio/images/{ancestor.id}/content"
								alt={ancestor.provenance?.prompt ?? 'An uploaded image'}
								class="size-14 rounded-md border border-border object-cover"
							/>
						</button>
					</li>
				{/each}
			</ol>
		</section>
	{/if}
</div>
