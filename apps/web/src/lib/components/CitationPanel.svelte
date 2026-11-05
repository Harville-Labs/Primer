<script lang="ts">
	import { Badge, Card, Sheet } from '@sivir-ui/svelte';
	import type { Citation, DocumentSummary } from '#lib/api/types.ts';
	import { describeCitation } from '#lib/export/markdown.ts';

	interface Props {
		citations: Citation[];
		//: The cited documents by id, for their names and to show an image.
		documents?: Map<string, DocumentSummary>;
		/** Bound, so the parent opens it and Sheet closes it. */
		open?: boolean;
		/**
		 * A passage to scroll to and mark, when the reader arrived here by
		 * clicking its marker in the answer rather than by opening the
		 * panel.
		 *
		 * The token is what makes asking for the same passage twice work.
		 * Without it the second request is the same value assigned again
		 * and nothing happens - which is exactly the case that needs to:
		 * the reader has scrolled away and is clicking the marker to get
		 * back to where it pointed.
		 */
		reveal?: { position: number; token: number } | null;
	}

	let { citations, documents = new Map(), open = $bindable(false), reveal = null }: Props = $props();

	let list = $state<HTMLElement | null>(null);
	//: Which passage is marked, until another is asked for. It stays rather
	//: than flashing: the reader followed a marker to compare a claim
	//: against its source, and that comparison is not over in two seconds.
	let marked = $state<number | null>(null);

	$effect(() => {
		if (!open) {
			marked = null;
			return;
		}
		const asked = reveal;
		if (!asked) return;
		marked = asked.position;
		// On the next frame: the sheet animates in, and scrolling a panel
		// that has not been laid out yet scrolls nothing.
		const frame = requestAnimationFrame(() => {
			list
				?.querySelector(`[data-citation="${asked.position}"]`)
				?.scrollIntoView({ block: 'center', behavior: 'smooth' });
		});
		return () => cancelAnimationFrame(frame);
	});

	function documentFor(citation: Citation): DocumentSummary | undefined {
		return documents.get(citation.document_id);
	}
</script>

<!--
  A panel, not a tooltip: a reader checking a citation wants to compare the
  quoted passage against the claim, which means both need to stay on screen.
  Sheet is focus-trapped and closes on Escape, so it is reachable and
  escapable from the keyboard.
-->
<Sheet.Root bind:open>
	<Sheet.Content side="right" class="w-full max-w-md">
		<Sheet.Header>
			<Sheet.Title>Sources</Sheet.Title>
			<Sheet.Description>
				The passages this answer was drawn from, in the order they were cited.
			</Sheet.Description>
		</Sheet.Header>

		<!--
	  Keeps its own gesture, like the transcript: this opens over the
	  conversation, and a flick that reached its end used to scroll whatever
	  was behind the sheet.
	-->
	<div bind:this={list} class="flex flex-col gap-3 overflow-y-auto overscroll-contain p-4">
			{#each citations as citation, index (citation.chunk_id)}
				{@const position = index + 1}
				{@const source = documentFor(citation)}
				<!--
				  "both" counts: the graph agreeing with the vector search is
				  part of why a passage ranked where it did. An answer stored
				  before provenance was recorded has none, and says nothing.
				-->
				{@const viaGraph = citation.found_by === 'graph' || citation.found_by === 'both'}
				<!--
				  The marker in the answer and this card are the same thing
				  seen twice, so the number is what ties them together and is
				  what the answer's link carries.
				-->
				<div
					data-citation={position}
					class="rounded-lg transition-shadow duration-200
						{marked === position ? 'shadow-[0_0_0_2px_var(--color-primary)]' : ''}"
				>
					<Card.Root>
						<Card.Header>
							<div class="flex items-center gap-2">
								<Badge variant={marked === position ? 'primary' : 'secondary'}>{position}</Badge>
								<span class="text-sm font-medium">
									{describeCitation(citation, source?.filename)}
								</span>
								{#if viaGraph}
									<Badge variant="outline">via graph</Badge>
								{/if}
							</div>
							<!--
							  Written out rather than tucked into a hover: the
							  entities are why an unexpected passage is here,
							  and a reader on a keyboard or a touch screen
							  needs them as much as one with a mouse.
							-->
							{#if viaGraph && citation.graph_entities.length > 0}
								<p class="text-xs text-muted-foreground">
									Connected through: {citation.graph_entities.join(', ')}
								</p>
							{/if}
						</Card.Header>
						{#if source?.media_type === 'image/png'}
							<Card.Content>
								<!-- Through SvelteKit's proxy, like a download: the browser never reaches Control. -->
								<img
									src="/libraries/{source.library_id}/documents/{source.id}/content"
									alt={source.filename}
									loading="lazy"
									class="max-h-48 rounded-md border border-border bg-muted object-contain"
								/>
							</Card.Content>
						{/if}
						{#if citation.excerpt}
							<Card.Content>
								<!--
								  The document's own words, quoted. Never a
								  storage key: how the deployment stores a file
								  is not part of the citation, and showing it
								  would leak layout.
								-->
								<blockquote class="border-l-2 border-border pl-3 text-sm text-muted-foreground">
									{citation.excerpt}
								</blockquote>
							</Card.Content>
						{/if}
					</Card.Root>
				</div>
			{/each}
		</div>
	</Sheet.Content>
</Sheet.Root>
