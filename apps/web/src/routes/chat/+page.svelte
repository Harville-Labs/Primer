<script lang="ts">
	import {
		Alert,
		Attachment,
		Button,
		Composer,
		Conversation,
		Progress,
		Toggle
	} from '@sivir-ui/svelte';
	import { Library, Plus } from '@lucide/svelte';
	import { afterNavigate, goto, invalidateAll, replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import { untrack } from 'svelte';
	import CitationPanel from '#lib/components/CitationPanel.svelte';
	import LibraryLink from '#lib/components/LibraryLink.svelte';
	import ModelPicker from '#lib/components/ModelPicker.svelte';
	import ChatTurn from '#lib/components/ChatTurn.svelte';
	import UploadNotices from '#lib/components/UploadNotices.svelte';
	import { parseEvents, type StreamState } from '#lib/api/sse.ts';
	import type { ConversationSummary, MessageSummary } from '#lib/api/types.ts';
	import { draft } from '#lib/draft.svelte.ts';
	import { modelOf, unqualify } from '#lib/models.ts';
	import { modelChanges } from '#lib/transcript.ts';
	import { Transcript } from '#lib/transcript.svelte.ts';
	import { ChatUploads } from '#lib/chat-uploads.svelte.ts';
	import { discardLibrary, uploadDocument } from '#lib/upload-client.ts';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Empty means no library, which is a usable state rather than a missing
	// one: the model answers on its own. Nothing is chosen by default,
	// because silently attaching the first library would make an answer look
	// grounded in something the user never picked.
	let libraryId = $state('');
	// Empty means the deployment's default, which is also what a request that
	// names no model gets. Not defaulted to a name here: the server decides
	// what its default is, and copying that choice into the client would let
	// the two disagree.
	let model = $state('');
	let question = $state('');
	// Whether the next question reads the passages nearest it or passages
	// from across the library. Off by default and never guessed from the
	// wording: the two answers differ enough that the user should know which
	// one they asked for. See ADR 0006.
	let acrossLibrary = $state(false);
	// The turns, and the machinery for writing one as it arrives. Held
	// there rather than here because folding a stream into a turn is the
	// one thing on this page that has to be careful about how Svelte sees
	// a change; `transcript.svelte.ts` says why.
	const transcript = new Transcript();
	// Set by the first answer and sent with every question after it. Without
	// it each question opened its own conversation, so the model never saw
	// the turn before - and a follow-up like "and the second one?" had
	// nothing to resolve against.
	let conversationId = $state<string | null>(null);
	let streaming = $state(false);
	let sourcesOpen = $state(false);
	let sourcesFor = $state<StreamState | null>(null);
	//: Which passage the panel should scroll to, when it was opened by
	//: following a marker rather than by asking for the sources.
	let revealed = $state<{ position: number; token: number } | null>(null);
	let revealToken = 0;

	// The library is fixed when the conversation opens: follow-up questions
	// carry only the conversation, so changing it now would change nothing.
	let started = $derived(conversationId !== null);
	let dragging = $state(false);
	let linkedName = $derived(data.libraries.find((library) => library.id === libraryId)?.name);
	let announcement = $state('');
	const uploads = new ChatUploads({
		upload: uploadDocument,
		discard: discardLibrary,
		announce: (message) => (announcement = message)
	});
	//: Bumped whenever the thread on screen is replaced, so each turn's own
	//: state goes with it rather than passing to the turn at the same
	//: position in the next thread.
	let thread = $state(0);

	//: Which stored conversation this screen is currently showing, so that
	//: reloading the list does not look like opening a different thread.
	let shown = $state<string | null>(null);

	//: An answer that has been asked for but has not started arriving. Once
	//: the first token lands the text itself is the progress, so the bar
	//: gives way to it rather than running alongside.
	let thinking = $derived(streaming && (transcript.last?.stream.text ?? '') === '');

	/**
	 * Everything that belongs to the thread on screen, cleared.
	 *
	 * The library is deliberately not here. Whether it survives depends on
	 * how the thread was left, and the two callers disagree: opening
	 * another conversation takes that conversation's library, while
	 * starting over keeps the one at hand.
	 */
	function clearThread() {
		shown = null;
		conversationId = null;
		transcript.clear();
		question = '';
		sourcesFor = null;
		sourcesOpen = false;
		revealed = null;
		uploads.clear();
		thread += 1;
	}

	/**
	 * Show a stored conversation, replacing whatever is on screen.
	 *
	 * The only thing that writes over a transcript, and it is only ever
	 * called with one that a load actually returned.
	 */
	function show(
		opened: { conversation: ConversationSummary; messages: MessageSummary[] } | null
	) {
		clearThread();
		libraryId = opened?.conversation.library_id ?? '';
		if (!opened) return;
		shown = opened.conversation.id;
		conversationId = shown;
		transcript.open(opened.messages);
		// Come back on the model that answered, not the deployment's
		// default. Every question carries the picker's value, so a thread
		// reopened on the default sent its next question somewhere other
		// than the rest of the conversation had gone - silently, since the
		// picker showed the model it was about to use rather than the one
		// the answers above it came from.
		//
		// Left alone when the conversation recorded nothing, which is an
		// answer from before the model was kept.
		const answered = modelOf(opened.messages);
		if (answered) model = answered;
	}

	// Follows the load, and deliberately cannot clear anything.
	//
	// It used to also handle the empty case - a load that opened nothing
	// meant "clear the screen" - and that is what was throwing answers away.
	// Three quite different things arrive here looking identical: a new chat
	// being started, a load that failed to open the thread it was asked for,
	// and a refresh that had not yet caught up with a conversation written a
	// moment earlier. Only the first means clear, and the effect cannot tell
	// them apart, because by the time it runs the difference is gone.
	//
	// So it no longer tries. Adopting a conversation is the only thing that
	// happens here; clearing is `afterNavigate`'s job below, which knows
	// something this does not - that a person deliberately went somewhere.
	$effect(() => {
		const opened = data.opened;
		if (!opened) return;
		if (untrack(() => shown) === opened.conversation.id) return;
		untrack(() => show(opened));
	});

	// Starting a new chat, when it is a link rather than the button.
	//
	// The sidebar's "New chat" is an anchor to /chat, so nothing on this page
	// runs when it is clicked - the component is already mounted and only the
	// data changes. This is the signal that was missing.
	//
	// A navigation is exactly the right thing to listen for, and the reason
	// the effect above cannot do this job: invalidating the sidebar is not a
	// navigation, and neither is the shallow URL update after an answer, so
	// neither of them reaches here. Only a person going somewhere does.
	afterNavigate(({ to }) => {
		if (to?.url.pathname !== '/chat') return;
		if (to.url.searchParams.get('conversation') !== null) return;
		// Already blank - and this fires on the shallow update too, where
		// clearing would throw away the answer that just arrived.
		if (untrack(() => shown) === null && untrack(() => transcript.length) === 0) return;
		show(null);
	});

	//: True when nothing can answer a question. Asking anyway would spend a
	//: user's time on a request that cannot succeed, so the composer is shut
	//: rather than left inviting.
	let answerable = $derived(data.models.length > 0);

	async function accept(files: File[]) {
		libraryId = await uploads.accept(files, {
			libraryId,
			libraryName: linkedName,
			started,
			capabilities: data.capabilities
		});
		// The sidebar counts documents, and it is on this page too. Safe to
		// refresh everything now: the effect above adopts conversations and
		// never clears them, so a reload cannot take the answers off screen.
		await invalidateAll();
	}

	async function discard() {
		if (!uploads.offered) return;
		libraryId = '';
		await uploads.discard();
		await invalidateAll();
	}

	async function ask() {
		const asked = question.trim();
		if (!asked || streaming || !answerable) return;

		question = '';
		streaming = true;
		// The timeline should say where you are from the first question, not
		// from the first answer. A conversation is not stored until the
		// question is asked, so until it is, this stands in for it.
		if (!conversationId) draft.begin(asked);
		// The bar is a picture of this, and a picture announces nothing.
		announcement = 'Waiting for an answer.';
		const answer = transcript.ask(asked);

		try {
			const response = await fetch('/chat/ask', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				// Omitted rather than sent empty when nothing is linked: the
				// absence of a library is what asks for an uncited answer.
				// Fields are omitted rather than sent empty: no library asks for
				// an uncited answer, and no model asks for the default.
				body: JSON.stringify({
					message: asked,
					...(conversationId ? { conversation_id: conversationId } : {}),
					...(libraryId ? { library_id: libraryId } : {}),
					// Sent with every question, follow-ups included, because Chat
					// reads it per question and treats a missing one as focused.
					...(libraryId && acrossLibrary ? { breadth: 'library' } : {}),
					// Split back into the two fields the request carries. The
					// picker's value names both, because a model name alone
					// cannot say which provider serves it.
					...unqualify(model)
				})
			});
			if (!response.ok || !response.body) throw new Error('The server refused the question.');

			for await (const event of parseEvents(response.body)) answer.apply(event);
			// Taken from the answer rather than assumed, so the next question
			// continues the conversation the server actually opened.
			conversationId = answer.stream.conversationId ?? conversationId;
			if (conversationId && shown !== conversationId) {
				// Shallow, so the thread on screen is not torn down and rebuilt
				// from storage mid-conversation. It puts the thread in the URL,
				// which is what makes a refresh or a bookmark come back to it.
				shown = conversationId;
				replaceState(`/chat?conversation=${conversationId}`, page.state);
			}
			// The history list is a page load away from knowing this happened.
			// It is also, now, guaranteed to see it: Control commits a write
			// before answering, so this reload cannot race the conversation
			// that was just created.
			await invalidateAll();
			// Stored now, so the real entry takes over from the stand-in.
			draft.settle();
		} catch {
			answer.fail('connection_lost', 'The connection closed before the answer finished.');
		} finally {
			streaming = false;
			// Cleared however the turn ended. A question that failed left no
			// conversation behind, so a stand-in for one would be a row
			// pointing at nothing.
			draft.settle();
			announcement = answer.stream.error ? 'The answer stopped early.' : 'Answer received.';
		}
	}

	// The library and the model are kept: starting over is usually asking
	// something else about the same documents, not changing what is at hand.
	async function startOver() {
		clearThread();
		draft.settle();
		announcement = 'Started a new chat.';
		// The conversation that was open is in the URL, and leaving it there
		// would restore the thread on the next reload.
		if (page.url.searchParams.has('conversation')) await goto('/chat');
	}

	//: Where the model answering changed, so the transcript can say so.
	let switches = $derived(modelChanges(transcript.turns));

	/**
	 * Open the sources panel on one answer.
	 *
	 * A position is passed only when the reader arrived by following a
	 * marker. Asking for the sources outright opens the list at the top,
	 * because nothing in particular was asked about - and a fresh token
	 * every time, so following the same marker twice scrolls back to it
	 * rather than doing nothing.
	 */
	function showSources(stream: StreamState, position: number | null = null) {
		sourcesFor = stream;
		sourcesOpen = true;
		revealed = position === null ? null : { position, token: ++revealToken };
	}
</script>

<!--
  The chat fills the height it is given, so the composer sits at the bottom
  of the frame rather than under the conversation wherever that happens to
  end.
-->
<div class="flex h-full min-h-0 flex-col">
	<!--
	  A header for the thread, above the thread. What answers a question is
	  the model, and which model that is was previously never on screen at
	  all - the picker hid itself whenever a deployment served only one, so
	  the common case showed nothing. It is stated here whether or not there
	  is a choice to make, because "which model wrote this" is a question
	  about the answer in front of you rather than a setting.
	-->
	<header class="shrink-0 border-b border-border pb-2">
		<div class="mx-auto flex w-full max-w-3xl items-center justify-between gap-3">
			<ModelPicker models={data.models} problem={data.modelsProblem} bind:value={model} />

			<!--
			  The way back to a blank page, and the only way to change the
			  library a conversation is answered from. Absent until there is
			  something to leave behind.
			-->
			{#if transcript.length > 0}
				<Button size="sm" variant="ghost" onclick={startOver} disabled={streaming}>
					<Plus size={14} aria-hidden="true" />
					New chat
				</Button>
			{/if}
		</div>

		<!--
		  A question can sit unanswered for a long time on a small model, and
		  until now nothing on the page moved while it did: the composer went
		  quiet and the conversation simply stopped, which reads as a request
		  that was dropped rather than one being worked on. The bar runs from
		  asking until the first token arrives, and then hands over to the
		  text, which is a better progress indicator than any bar.

		  It keeps its height when idle so the header does not jump by a
		  pixel and a half every time a question is asked.
		-->
		<div class="mx-auto mt-2 h-[3px] w-full max-w-3xl">
			{#if thinking}
				<Progress indeterminate class="h-[3px]" />
			{/if}
		</div>
	</header>

	{#if data.modelsProblem}
		<!--
		  On the page, not in a toast. A deployment with no model stays broken
		  until someone changes something, and a message that fades after five
		  seconds is one nobody who arrives later ever sees. It names the
		  endpoint, because the person reading this is usually the person who
		  has to go and fix it.
		-->
		<Alert.Root variant="error" class="mx-auto mt-4 w-full max-w-3xl">
			<Alert.Title>No model can answer questions</Alert.Title>
			<Alert.Description>{data.modelsProblem}</Alert.Description>
		</Alert.Root>
	{/if}

	{#if data.unopened}
		<!--
		  Said out loud. A thread that simply appeared blank would look like a
		  conversation that lost its messages, which is a much worse thing to
		  believe than either of these.
		-->
		<Alert.Root variant={data.unopened === 'missing' ? 'warning' : 'error'} class="mt-4">
			<Alert.Description>
				{data.unopened === 'missing'
					? 'That conversation is no longer here. This is a new one.'
					: 'That conversation could not be opened. It is still stored; try again in a moment.'}
			</Alert.Description>
		</Alert.Root>
	{/if}

<!--
  Constrained here rather than in the frame. Answers are prose, and prose set
  the full width of a desktop window is unreadable - but the frame around it
  holds tables that need every pixel.
-->
<!--
  The whole conversation is the drop target, not a separate upload control.
  Dropping a file into a chat is how people expect to add something to what
  they are talking about, and a dropzone beside it would be a second place to
  do the same thing.
-->
<div
	class="relative mx-auto mt-6 flex min-h-0 w-full max-w-3xl flex-1 flex-col"
	ondragover={(event) => {
		event.preventDefault();
		dragging = true;
	}}
	ondragleave={(event) => {
		// Only when the pointer leaves the region itself: moving between
		// children fires dragleave constantly and would flicker the overlay.
		if (!event.currentTarget.contains(event.relatedTarget as Node)) dragging = false;
	}}
	ondrop={(event) => {
		event.preventDefault();
		dragging = false;
		void accept([...(event.dataTransfer?.files ?? [])]);
	}}
	role="presentation"
>
	{#if dragging}
		<!--
		  Named rather than a bare highlight, because where the file lands is
		  the thing in question: an existing library, or a new one.
		-->
		<div
			class="pointer-events-none absolute inset-0 z-20 flex items-center justify-center
				rounded-lg border-2 border-dashed border-primary bg-background/85 text-sm font-medium"
		>
			{linkedName
				? `Add to ${linkedName}`
				: 'Drop to start a library from this file'}
		</div>
	{/if}

	<Conversation.Root class="flex-1">
		<!--
		  The transcript is what a reader scrolls, so it keeps the gesture:
		  reaching the top or the bottom stops there rather than passing it
		  outward. On a phone that was the difference between scrolling the
		  conversation and dragging the whole interface around behind it.
		-->
		<Conversation.Content class="overscroll-contain">
			{#if transcript.length === 0}
				<Conversation.Empty>
					{#if libraryId}
						Ask a question about this library. Every answer cites the passages it used.
					{:else}
						Ask anything. Link a library below to have answers drawn from your own
						documents and cited.
					{/if}
				</Conversation.Empty>
			{/if}

			{#key thread}
				{#each transcript.turns as turn, index (index)}
					<ChatTurn {turn} switchedTo={switches[index]} onshowsources={showSources} />
				{/each}
			{/key}
		</Conversation.Content>
		<Conversation.ScrollButton />
	</Conversation.Root>

	<Composer.Root
		bind:value={question}
		disabled={!answerable || streaming}
		onSubmit={ask}
		class="mt-4"
	>
		<Composer.Input
			placeholder={!answerable
				? 'No model is available to answer.'
				: libraryId
					? 'Ask about this library…'
					: 'Ask anything…'}
			aria-label="Your question"
		/>
		<Composer.Toolbar>
			<Composer.Actions></Composer.Actions>
			<Composer.Submit />
		</Composer.Toolbar>
	</Composer.Root>

	<!--
	  Below the composer, because whether this question is answered from your
	  documents belongs with asking it rather than with the page around it.
	-->
	<div class="mt-2 flex flex-wrap items-center justify-between gap-3">
		<div class="flex flex-wrap items-center gap-2">
			<LibraryLink libraries={data.libraries} bind:value={libraryId} locked={started} />
			{#if libraryId}
				<Toggle
					bind:pressed={acrossLibrary}
					size="sm"
					variant="outlined"
					title="Answer from passages across the whole library, not only the nearest ones"
				>
					<Library class="size-4" aria-hidden="true" />
					Across the library
				</Toggle>
			{/if}
			{#each uploads.pending as upload (upload.id)}
				<!--
				  A preview of the document it is about to become rather than a
				  bare spinner - what is happening is that this file specifically
				  is becoming part of the library, not that work of some kind is in
				  progress.
				-->
				<Attachment.Item
					file={upload.file}
					status={upload.status}
					removable={false}
					class="max-w-[14rem]"
				/>
			{/each}
		</div>

		{#if !libraryId}
			<p class="mr-12 text-xs text-muted-foreground sm:mr-0">Answers will not be cited.</p>
		{/if}
	</div>

	<!--
	  Uploads happen without the user reading this corner of the page, so they
	  are announced politely rather than left to be noticed.
	-->
	<p class="sr-only" aria-live="polite">{announcement}</p>

	<UploadNotices {uploads} ondiscard={discard} />
</div>
</div>

{#if sourcesFor}
	<CitationPanel citations={sourcesFor.citations} bind:open={sourcesOpen} reveal={revealed} />
{/if}
