<script lang="ts">
	import { enhance } from '$app/forms';
	import { Copy, Plus, Trash2, Users } from '@lucide/svelte';
	import { Alert, Button, Input } from '@sivir-ui/svelte';
	import { owns } from '#lib/libraries.ts';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let total = $derived(data.libraries.reduce((sum, library) => sum + library.document_count, 0));

	function mine(library: { owner_user_id: string }): boolean {
		return owns(data.principal, library);
	}
</script>

<!--
  Title and the one action on this page share a line. The create field is
  inline rather than behind a dialog: naming a library is the whole of it,
  and a modal to type one word is ceremony.
-->
<div class="flex flex-wrap items-end justify-between gap-4">
	<div>
		<h1 class="text-xl font-semibold tracking-[-0.02em]">Libraries</h1>
		<p class="mt-1 font-mono text-[11px] uppercase tracking-[0.09em] text-muted-foreground">
			{data.libraries.length}
			{data.libraries.length === 1 ? 'library' : 'libraries'} · {total}
			{total === 1 ? 'document' : 'documents'}
		</p>
	</div>

	<!--
	  Full width on a narrow screen, sized to its content once there is room.
	  A fixed-width field beside a button is wider than a small phone, and the
	  overflow moves the whole page rather than just this row.
	-->
	<form
		method="POST"
		action="?/create"
		use:enhance
		class="flex w-full items-center gap-2 sm:w-auto"
	>
		<Input
			name="name"
			required
			maxlength={120}
			placeholder="New library…"
			value={form?.name ?? ''}
			aria-label="New library name"
			class="min-w-0 flex-1 sm:w-56 sm:flex-none"
			aria-describedby={form?.error ? 'create-error' : undefined}
			aria-invalid={form?.error ? 'true' : undefined}
		/>
		<Button type="submit">
			<Plus size={15} aria-hidden="true" />
			Create
		</Button>
	</form>
</div>

{#if form?.error}
	<div id="create-error" class="mt-4">
		<Alert.Root variant="error">
			<Alert.Description>{form.error}</Alert.Description>
		</Alert.Root>
	</div>
{/if}

{#if data.libraries.length === 0}
	<div class="mt-16 text-center">
		<p class="font-medium">No libraries yet</p>
		<p class="mx-auto mt-1.5 max-w-md text-sm text-muted-foreground">
			A library is a private collection of sources. Name one above, add documents to it, and
			answers will be drawn from it alone — always citing the passages they used.
		</p>
	</div>
{:else}
	<!--
	  A grid, not a list. These are things you pick between rather than read
	  down, and on a wide screen a single column of names leaves the page
	  mostly empty while hiding the ones below the fold.
	-->
	<ul class="mt-8 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
		{#each data.libraries as library (library.id)}
			<li class="group relative">
				<a
					href="/libraries/{library.id}"
					class="flex h-full flex-col justify-between gap-6 rounded-lg border border-border
						bg-card p-4 transition-colors hover:border-border-strong
						focus-visible:outline-none focus-visible:shadow-[var(--focus-ring)]"
				>
					<span class="truncate pr-8 font-medium" title={library.name}>{library.name}</span>
					<span
						class="flex items-center gap-2 font-mono text-[11px] uppercase
							tracking-[0.09em] text-muted-foreground"
					>
						<span>
							{library.document_count}
							{library.document_count === 1 ? 'document' : 'documents'}
						</span>
						{#if !mine(library)}
							<!--
							  Marked, because the cards are otherwise identical
							  and what you may do with them is not: someone
							  else's library cannot be deleted or added to, and
							  finding that out by clicking is a worse way to
							  learn it.
							-->
							<span aria-hidden="true">·</span>
							<span class="flex items-center gap-1">
								<Users size={11} aria-hidden="true" />
								Shared
							</span>
						{/if}
					</span>
				</a>

				<!--
				  Positioned over the card rather than inside it, because a
				  button nested in a link is not a valid control anywhere.
				  Always present, not revealed on hover: a destructive action
				  that appears only under a pointer is unreachable by touch.
				-->
				<div class="absolute right-2 top-2 flex items-center">
					<form method="POST" action="?/duplicate" use:enhance>
						<input type="hidden" name="id" value={library.id} />
						<Button
							type="submit"
							variant="ghost"
							size="icon"
							title="Duplicate"
							class="text-muted-foreground opacity-60 transition-opacity hover:opacity-100"
						>
							<Copy size={14} aria-hidden="true" />
							<span class="sr-only">Duplicate {library.name}</span>
						</Button>
					</form>

					{#if mine(library)}
						<!--
						  Only for a library you own. Deleting one shared with
						  you is refused by Control, so offering it would be a
						  button whose only outcome is an error - and a
						  frightening one to click on someone else's material.
						  Duplicating stays: a copy of something you may read
						  is yours, and it is how you keep a library that
						  might be unshared.
						-->
						<form
							method="POST"
							action="?/delete"
							use:enhance={({ cancel }) => {
								if (!confirm(`Delete "${library.name}" and everything in it?`)) cancel();
							}}
						>
							<input type="hidden" name="id" value={library.id} />
							<Button
								type="submit"
								variant="ghost"
								size="icon"
								title="Delete"
								class="text-muted-foreground opacity-60 transition-opacity hover:opacity-100"
							>
								<Trash2 size={14} aria-hidden="true" />
								<span class="sr-only">Delete {library.name}</span>
							</Button>
						</form>
					{/if}
				</div>
			</li>
		{/each}
	</ul>
{/if}
