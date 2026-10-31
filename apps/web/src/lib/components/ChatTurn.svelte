<script lang="ts">
	import { Alert, Markdown, Message, Reasoning } from '@sivir-ui/svelte';
	import ResponseActions from '#lib/components/ResponseActions.svelte';
	import type { StreamState } from '#lib/api/sse.ts';
	import type { MessageSummary } from '#lib/api/types.ts';
	import { citationFrom, linkCitations } from '#lib/citations.ts';
	import type { Turn } from '#lib/transcript.ts';

	let {
		turn,
		switchedTo = null,
		onshowsources
	}: {
		turn: Turn;
		/** The model this turn changed to, when it is not the one before it. */
		switchedTo?: string | null;
		/** Open the sources panel, at a passage when a marker was followed. */
		onshowsources: (stream: StreamState, position: number | null) => void;
	} = $props();

	//: Whether the thinking is expanded, once someone has said so. Null
	//: means nobody has touched it, and the default applies: open while the
	//: model is still working, folded away once it has answered. Held here
	//: rather than in a page-level map keyed by position, which outlived the
	//: thread and handed a new chat's first answer the old one's choice.
	let thinkingOpen = $state<boolean | null>(null);

	//: A line of the thinking, shown on the folded header so it is worth
	//: opening. Newlines are flattened because the header is one line and a
	//: model's scratch work is full of them.
	const PREVIEW_CHARACTERS = 110;
	function thinkingPreview(reasoning: string): string {
		const flattened = reasoning.replace(/\s+/g, ' ').trim();
		return flattened.length > PREVIEW_CHARACTERS
			? `${flattened.slice(0, PREVIEW_CHARACTERS).trimEnd()}…`
			: flattened;
	}

	/**
	 * Following a citation marker in an answer to the passage it names.
	 *
	 * Delegated from a wrapper rather than bound to each link, because the
	 * links are inside rendered Markdown: this component hands the renderer
	 * text and does not own what it produces. A link the model wrote itself
	 * falls through and behaves like a link.
	 */
	function followCitation(event: MouseEvent) {
		const anchor = (event.target as Element | null)?.closest('a');
		const position = citationFrom(anchor?.getAttribute('href'));
		if (position === null || position > turn.stream.citations.length) return;
		event.preventDefault();
		onshowsources(turn.stream, position);
	}

	function completed(stream: StreamState): MessageSummary | null {
		if (stream.message) return stream.message;
		// A failed stream still has an answer worth copying: the text it
		// managed to write before it stopped.
		if (!stream.error || !stream.messageId) return null;
		return {
			id: stream.messageId,
			conversation_id: stream.conversationId ?? '',
			role: 'assistant',
			state: 'failed',
			content: stream.text,
			citations: stream.citations,
			error_code: stream.error.code,
			created_at: new Date().toISOString()
		};
	}
</script>

{#if switchedTo}
	<!--
	  Which model wrote an answer is part of the answer, and in a conversation
	  where someone changed it halfway it is the thing that explains why two
	  answers do not sound alike. Above the question, because the choice was
	  made before it was asked. Quiet: it is a note about the transcript, not a
	  turn in it.
	-->
	<div class="my-3 flex items-center gap-3 text-xs text-muted-foreground">
		<span class="h-px flex-1 bg-border"></span>
		<span>Switched model to {switchedTo}</span>
		<span class="h-px flex-1 bg-border"></span>
	</div>
{/if}

<Message.Root from="user">
	<Message.Content>{turn.question}</Message.Content>
</Message.Root>

<Message.Root from="assistant" status={turn.stream.done ? 'idle' : 'streaming'}>
	{#if turn.stream.reasoning !== null}
		<!--
		  What the model worked through, above the answer it produced and
		  folded away once it has. Null rather than empty is what keeps this
		  off the screen entirely for the models that do not think aloud,
		  which is most of them.

		  Quiet on purpose: it is scratch work, not the answer, and it must not
		  compete with the thing the reader actually asked for.
		-->
		<Reasoning.Root
			streaming={!turn.stream.done}
			open={thinkingOpen ?? !turn.stream.done}
			onOpenChange={(open) => (thinkingOpen = open)}
			class="mb-2"
		>
			<!--
			  Once the answer settles, the folded header carries a line of the
			  thinking itself. The kit's own settled label is the elapsed time,
			  which tells a reader nothing about whether this one is worth
			  opening.
			-->
			<Reasoning.Trigger summary={thinkingPreview(turn.stream.reasoning)} />
			<!--
			  `overflow-wrap:anywhere` because thinking is where a model writes
			  out a hash, a path or a URL unbroken, and `pre-wrap` alone will not
			  break inside a word. The kit sets this on an answer already;
			  reasoning is rendered here rather than through it.
			-->
			<Reasoning.Content
				class="whitespace-pre-wrap [overflow-wrap:anywhere] border-l border-border pl-3 text-xs
					leading-relaxed text-muted-foreground"
			>
				{turn.stream.reasoning}
			</Reasoning.Content>
		</Reasoning.Root>
	{/if}

	<Message.Content>
		<!--
		  Rendered as Markdown, since models write it. The text is the model's,
		  so it is rendered rather than interpreted: nothing here acts on it.

		  The wrapper catches clicks on the citation markers, which are
		  rewritten into links on the way in. An anchor raises a click on Enter
		  as well as on a pointer, so this is reached from the keyboard too.
		-->
		<div role="presentation" onclick={followCitation}>
			<Markdown content={linkCitations(turn.stream.text, turn.stream.citations.length)} />
		</div>
	</Message.Content>

	{#if turn.stream.error}
		<Alert.Root variant="error" class="mt-2">
			<Alert.Description>
				{turn.stream.error.detail ?? 'The answer stopped before it finished.'}
			</Alert.Description>
		</Alert.Root>
	{/if}

	{#if turn.stream.done}
		{@const message = completed(turn.stream)}
		{#if message}
			<Message.Actions>
				<ResponseActions {message} onshowsources={() => onshowsources(turn.stream, null)} />
			</Message.Actions>
		{/if}
	{/if}
</Message.Root>
