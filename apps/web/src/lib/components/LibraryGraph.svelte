<script lang="ts">
	import { Alert, Card, Spinner } from '@sivir-ui/svelte';
	import type { DocumentSummary } from '#lib/api/types.ts';
	import { type GraphReading, withoutGraph } from '#lib/graph-status.ts';

	interface Props {
		reading: Promise<GraphReading>;
		documents: DocumentSummary[];
		/** Whether this person can reindex, which decides what the card asks of them. */
		owned: boolean;
	}

	let { reading, documents, owned }: Props = $props();

	const formatted = new Intl.NumberFormat();
</script>

<!--
  What the knowledge graph holds, beside the documents it was built from.
  Counts rather than a picture of the graph: the question a reader has here
  is whether the graph covers their library, and a drawing of ten thousand
  entities does not answer it.
-->
<Card.Root class="mt-8">
	<Card.Header>
		<Card.Title>Knowledge graph</Card.Title>
		<Card.Description>
			The people, places, and ideas your documents mention, and how they connect. Answers use it
			to find passages that do not share words with the question.
		</Card.Description>
	</Card.Header>
	<Card.Content>
		{#await reading}
			<p class="flex items-center gap-2 text-sm text-muted-foreground">
				<Spinner size={12} aria-hidden="true" /> Counting the graph.
			</p>
		{:then read}
			{#if 'error' in read}
				<Alert.Root variant="warning">
					<Alert.Description>{read.error}</Alert.Description>
				</Alert.Root>
			{:else}
				{@const missing = withoutGraph(documents, read.status)}
				<dl class="grid grid-cols-3 gap-4 text-sm">
					<div>
						<dt class="text-muted-foreground">Entities</dt>
						<dd class="text-lg font-medium tabular-nums">{formatted.format(read.status.entities)}</dd>
					</div>
					<div>
						<dt class="text-muted-foreground">Relations</dt>
						<dd class="text-lg font-medium tabular-nums">{formatted.format(read.status.relations)}</dd>
					</div>
					<div>
						<dt class="text-muted-foreground">Topics</dt>
						<dd class="text-lg font-medium tabular-nums">
							{read.status.communities === null || read.status.communities === undefined
								? '—'
								: formatted.format(read.status.communities)}
						</dd>
					</div>
				</dl>
				{#if read.status.communities === null || read.status.communities === undefined}
					<p class="mt-3 text-xs text-muted-foreground">
						This library is too large to group into topics, so questions about the whole library
						are unavailable. Focused questions still use the graph.
					</p>
				{/if}
				{#if missing.length > 0}
					<!--
					  Named, not just counted: the reader's next step is to
					  reindex these, and a number gives them nothing to act on.
					-->
					<div class="mt-4">
						<p class="text-sm font-medium">
							{missing.length}
							{missing.length === 1 ? 'document has' : 'documents have'} no graph
						</p>
						<p class="mt-1 text-xs text-muted-foreground">
							{missing.length === 1 ? 'It was' : 'They were'} indexed while no extraction model was
							configured, so answers find {missing.length === 1 ? 'it' : 'them'} by wording alone.
							{owned
								? 'Reindex once an extraction model is configured to add them to the graph.'
								: 'The library’s owner can reindex them to add them to the graph.'}
						</p>
						<ul class="mt-2 list-disc pl-5 text-sm">
							{#each missing as document (document.id)}
								<li>{document.filename}</li>
							{/each}
						</ul>
					</div>
				{/if}
			{/if}
		{/await}
	</Card.Content>
</Card.Root>
