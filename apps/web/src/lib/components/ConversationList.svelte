<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { Library, Link2Off, Trash2, TriangleAlert } from '@lucide/svelte';
	import { Button, Spinner } from '@sivir-ui/svelte';
	import type { ConversationSummary, LibrarySummary } from '#lib/api/types.ts';
	import { draft } from '#lib/draft.svelte.ts';
	import { notify } from '#lib/notifications.svelte.ts';
	import { exactly, timeAgo } from '#lib/when.ts';

	interface Props {
		conversations: ConversationSummary[];
		/** To name the library each was asked of, and to notice a missing one. */
		libraries: LibrarySummary[];
	}

	let { conversations, libraries }: Props = $props();

	//: The one being deleted, so it stops accepting clicks while it goes.
	let deleting = $state<string | null>(null);

	// Read from the URL rather than passed in. This list lives in the frame
	// now, so it is on screen when the chat page is not; the URL is the one
	// thing that knows which thread is open from anywhere.
	let openId = $derived(
		page.url.pathname === '/chat' ? page.url.searchParams.get('conversation') : null
	);

	/**
	 * What a conversation was asked of.
	 *
	 * Three states, not two. A conversation with no library was answered by
	 * the model alone and always will be; one whose library has since been
	 * deleted was grounded in documents that are gone, which is why its old
	 * answers cite things this deployment can no longer show. Collapsing
	 * those into "no library" would make a broken thread look like a
	 * deliberate one.
	 */
	function grounding(conversation: ConversationSummary) {
		if (conversation.library_id === null) {
			return { label: 'No library', icon: Link2Off, tone: 'text-muted-foreground' };
		}
		const library = libraries.find((candidate) => candidate.id === conversation.library_id);
		if (library) return { label: library.name, icon: Library, tone: 'text-muted-foreground' };
		return { label: 'Library deleted', icon: TriangleAlert, tone: 'text-error' };
	}

	/**
	 * Delete a thread from wherever the list happens to be.
	 *
	 * Handled here rather than handed up to a page, because the list now
	 * outlives any one of them: the sidebar shows it on the libraries screen
	 * too, and a delete that only worked on the chat page would be a control
	 * that does nothing depending on where you are.
	 */
	async function remove(conversation: ConversationSummary) {
		if (deleting) return;
		deleting = conversation.id;
		try {
			const response = await fetch(`/chat/conversations/${conversation.id}`, {
				method: 'DELETE',
			});
			if (!response.ok) throw new Error('That conversation could not be deleted.');
			// Leaving the screen showing a thread that no longer exists would
			// be a transcript nothing stands behind.
			if (conversation.id === openId) await goto('/chat');
			else await invalidateAll();
		} catch (error) {
			notify(
				'error',
				'That conversation could not be deleted.',
				error instanceof Error ? error.message : undefined
			);
		} finally {
			deleting = null;
		}
	}
</script>

{#if conversations.length === 0 && !draft.title}
	<!--
	  Said rather than left blank. An empty column reads as something that
	  failed to load, and the first thing a new user sees here is nothing.
	-->
	<p class="px-2.5 py-1 text-xs text-muted-foreground">
		Conversations you have had appear here, newest first.
	</p>
{/if}

<ul class="-mx-1 min-h-0 space-y-0.5 overflow-y-auto overscroll-contain px-1">
	{#if draft.title}
		<!--
		  The thread being written, before it has been stored. It is not a
		  link: there is nowhere else to go, it is already what is on screen.
		  Shown so the timeline says where you are from the first question
		  rather than from the first answer.
		-->
		<li>
			<span
				aria-current="page"
				class="block rounded-md bg-secondary px-2.5 py-1.5 font-medium text-foreground"
			>
				<span class="block truncate text-sm leading-snug">{draft.title}</span>
				<span class="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
					<Spinner size={11} aria-hidden="true" />
					Saving…
				</span>
			</span>
		</li>
	{/if}
	{#each conversations as conversation (conversation.id)}
		{@const asked = grounding(conversation)}
		{@const open = conversation.id === openId}
		{@const going = conversation.id === deleting}
		<li class="group relative {going ? 'pointer-events-none opacity-50' : ''}">
			<!--
			  A link, not a button: a conversation has a URL, and the
			  ordinary things one does with one - a new tab, a bookmark, the
			  back button - should work.
			-->
			<a
				href="/chat?conversation={conversation.id}"
				aria-current={open ? 'page' : undefined}
				class="block rounded-md px-2.5 py-1.5 pr-8 transition-colors
					focus-visible:outline-none focus-visible:shadow-[var(--focus-ring)]
					{open
					? 'bg-secondary font-medium text-foreground'
					: 'text-muted-foreground hover:bg-muted hover:text-foreground'}"
			>
				<!--
				  One line for the title, the grounding underneath. In a rail
				  this narrow a wrapped title costs more rows than it earns,
				  and the whole title is a hover away.
				-->
				<span class="block truncate text-sm leading-snug" title={conversation.title}>
					{conversation.title}
				</span>
				<span class="mt-0.5 flex items-center gap-1.5 text-[11px] {asked.tone}">
					<asked.icon size={11} aria-hidden="true" class="shrink-0" />
					<span class="truncate">{asked.label}</span>
					<span aria-hidden="true">·</span>
					<time datetime={conversation.updated_at} title={exactly(conversation.updated_at)}>
						{timeAgo(conversation.updated_at)}
					</time>
				</span>
			</a>

			<!--
			  Kept out of the link, because a delete control inside a link is
			  a click that does one of two very different things depending on
			  a pixel. Visible on hover and on focus, so it is reachable by
			  keyboard rather than only by pointer.
			-->
			<Button
				variant="ghost"
				size="icon"
				class="absolute right-0.5 top-1 opacity-0 transition-opacity
					group-hover:opacity-100 focus-visible:opacity-100"
				onclick={() => remove(conversation)}
			>
				<Trash2 size={13} aria-hidden="true" />
				<span class="sr-only">Delete “{conversation.title}”</span>
			</Button>
		</li>
	{/each}
</ul>
