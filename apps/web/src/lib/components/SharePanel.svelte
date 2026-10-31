<script lang="ts">
	import { enhance } from '$app/forms';
	import { Trash2, UserPlus } from '@lucide/svelte';
	import { Alert, Button } from '@sivir-ui/svelte';
	import type { LibraryShare } from '#lib/api/types.ts';

	interface Props {
		shares: LibraryShare[];
		/** The failure from the last share or revoke, if there was one. */
		error?: string | null;
		/** The address just shared with, so the panel can say it worked. */
		shared?: string | null;
	}

	let { shares, error = null, shared = null }: Props = $props();

	//: Cleared on submit rather than left holding the last address, so a
	//: second share does not start by deleting someone else's name.
	let email = $state('');

	function nameFor(share: LibraryShare): string {
		return share.display_name ?? share.email ?? share.user_id;
	}
</script>

<!--
  Sharing sits under the documents rather than beside the title. It is a
  thing an owner does occasionally and checks rarely, and putting it at the
  top would make every visit to a library open on a list of who else can see
  it - which reads as a warning rather than a setting.
-->
<section class="mt-10 border-t border-border pt-6">
	<h2 class="text-sm font-semibold tracking-[-0.01em]">Shared with</h2>
	<p class="mt-1 text-xs text-muted-foreground">
		<!--
		  Says what a share does and does not do. "Shared" means very
		  different things in different tools, and someone who assumed it
		  meant collaborative editing would be handing out more than they
		  meant to.
		-->
		People you name here can read this library and ask questions of it. They cannot add
		documents, rename it, delete it, or share it onward.
	</p>

	<form
		method="POST"
		action="?/share"
		use:enhance={() => {
			const submitted = email;
			return async ({ update }) => {
				await update();
				// Kept on a failure, so a mistyped address can be corrected
				// rather than retyped.
				if (submitted) email = '';
			};
		}}
		class="mt-3 flex flex-wrap items-center gap-2"
	>
		<label class="sr-only" for="share-email">Email address to share with</label>
		<input
			id="share-email"
			name="email"
			type="email"
			bind:value={email}
			required
			placeholder="colleague@example.edu"
			class="min-w-0 flex-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-sm
				placeholder:text-muted-foreground focus-visible:outline-none
				focus-visible:shadow-[var(--focus-ring)]"
		/>
		<Button type="submit" size="sm" disabled={!email.trim()}>
			<UserPlus size={14} aria-hidden="true" />
			Share
		</Button>
	</form>

	{#if error}
		<Alert.Root variant="error" class="mt-3">
			<Alert.Description>{error}</Alert.Description>
		</Alert.Root>
	{:else if shared}
		<Alert.Root variant="success" class="mt-3">
			<Alert.Description>This library is now readable by {shared}.</Alert.Description>
		</Alert.Root>
	{/if}

	{#if shares.length === 0}
		<p class="mt-4 text-xs text-muted-foreground">
			<!--
			  Named as the default rather than as an empty list. A library
			  nobody else can see is the normal state, not a missing one.
			-->
			Nobody else can see this library.
		</p>
	{:else}
		<ul class="mt-4 flex flex-col gap-1">
			{#each shares as share (share.user_id)}
				<li
					class="flex items-center justify-between gap-3 rounded-md border border-border
						bg-card px-3 py-2 text-sm"
				>
					<span class="flex min-w-0 flex-col leading-tight">
						<span class="truncate font-medium">{nameFor(share)}</span>
						{#if share.display_name && share.email}
							<span class="truncate text-xs text-muted-foreground">{share.email}</span>
						{/if}
					</span>
					<form
						method="POST"
						action="?/revoke"
						use:enhance={({ cancel }) => {
							// Asked, because the person on the other side is not
							// told and will simply find it gone.
							if (!confirm(`Stop sharing with ${nameFor(share)}?`)) cancel();
						}}
					>
						<input type="hidden" name="userId" value={share.user_id} />
						<Button
							type="submit"
							variant="ghost"
							size="icon"
							class="text-muted-foreground"
							title="Stop sharing"
						>
							<Trash2 size={14} aria-hidden="true" />
							<span class="sr-only">Stop sharing with {nameFor(share)}</span>
						</Button>
					</form>
				</li>
			{/each}
		</ul>
	{/if}
</section>
