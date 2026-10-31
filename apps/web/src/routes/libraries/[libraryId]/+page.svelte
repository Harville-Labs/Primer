<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { RefreshCw, Trash2, Users } from '@lucide/svelte';
	import { Alert, AlertDialog, Breadcrumb, Button, Spinner } from '@sivir-ui/svelte';
	import type { DocumentSummary } from '#lib/api/types.ts';
	import DocumentStatus from '#lib/components/DocumentStatus.svelte';
	import SharePanel from '#lib/components/SharePanel.svelte';
	import UploadDropzone from '#lib/components/UploadDropzone.svelte';
	import { eachLimited, UPLOAD_CONCURRENCY } from '#lib/concurrency.ts';
	import { isTerminal, pollDelayMs } from '#lib/status.ts';
	import { formatBytes } from '#lib/upload.ts';
	import { uploadDocument } from '#lib/upload-client.ts';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let uploading = $state<string[]>([]);
	let confirmingRebuild = $state(false);
	let rebuilding = $state(false);
	let announcement = $state('');
	let uploadError = $state('');

	let pending = $derived(data.documents.filter((doc) => !isTerminal(doc.status)));
	let ready = $derived(data.documents.filter((doc) => doc.status === 'ready').length);
	// Anything finished that is not ready needs a person: a failed parse, an
	// unreadable scan. Counted separately so it is visible without reading
	// every row.
	let problems = $derived(
		data.documents.filter((doc) => isTerminal(doc.status) && doc.status !== 'ready').length
	);

	async function upload(files: File[]) {
		uploadError = '';
		await eachLimited(files, UPLOAD_CONCURRENCY, async (file) => {
			uploading = [...uploading, file.name];
			announcement = `Uploading ${file.name}.`;
			try {
				await uploadDocument(file, data.library.id);
				announcement = `${file.name} uploaded and queued.`;
			} catch (error) {
				uploadError = error instanceof Error ? error.message : `${file.name} could not be uploaded.`;
				announcement = uploadError;
			} finally {
				uploading = uploading.filter((name) => name !== file.name);
			}
		});
		await invalidateAll();
	}

	//: Whether anything is still being worked on. A boolean rather than the
	//: list itself, because the effect below watches it: reading the list
	//: there means every reload it triggers is a new value, so the effect
	//: tears itself down and starts again on each one - and the backoff,
	//: which lives in that scope, restarts from zero every time. The promise
	//: of backing off then buys nothing: measured against a stub, a document
	//: left parsing cost a request a second indefinitely.
	let awaitingWork = $derived(pending.length > 0);

	// Poll only while something is unfinished, backing off as it drags on: a
	// ten-minute document should not cost hundreds of requests, and a
	// finished one should stop costing anything at all.
	$effect(() => {
		if (!awaitingWork) return;
		let attempt = 0;
		let cancelled = false;
		let timer: ReturnType<typeof setTimeout>;

		const tick = async () => {
			if (cancelled) return;
			await invalidateAll();
			attempt += 1;
			if (!cancelled) timer = setTimeout(tick, pollDelayMs(attempt));
		};

		timer = setTimeout(tick, pollDelayMs(attempt));
		return () => {
			cancelled = true;
			clearTimeout(timer);
		};
	});

	function describe(document: DocumentSummary): string {
		return `${document.filename}, ${formatBytes(document.byte_size)}`;
	}
</script>

<div class="flex flex-wrap items-end justify-between gap-4">
	<div class="min-w-0">
		<nav aria-label="Breadcrumb">
			<Breadcrumb.Root>
				<Breadcrumb.Item href="/libraries">Libraries</Breadcrumb.Item>
				<Breadcrumb.Separator />
				<!--
				  The last crumb is plain text, not a link: a link to the page
				  you are already on is a dead end for anyone navigating by
				  links.
				-->
				<span aria-current="page" class="truncate font-semibold text-foreground">
					{data.library.name}
				</span>
			</Breadcrumb.Root>
		</nav>

		<h1 class="mt-2 truncate text-xl font-semibold tracking-[-0.02em]">{data.library.name}</h1>

		<!--
		  A counted summary rather than a bare total: "3 documents" hides that
		  one of them failed, which is the thing worth acting on.
		-->
		<p class="mt-1 flex flex-wrap items-center gap-x-2 font-mono text-[11px] uppercase
			tracking-[0.09em] text-muted-foreground">
			<span>{data.documents.length} {data.documents.length === 1 ? 'document' : 'documents'}</span>
			{#if ready > 0}<span aria-hidden="true">·</span><span>{ready} ready</span>{/if}
			{#if pending.length > 0}
				<span aria-hidden="true">·</span>
				<span class="flex items-center gap-1.5 text-foreground">
					<Spinner size={11} aria-hidden="true" />
					{pending.length} processing
				</span>
			{/if}
			{#if problems > 0}
				<span aria-hidden="true">·</span>
				<span class="text-error">{problems} needing attention</span>
			{/if}
		</p>
	</div>

	{#if data.owned}
		<div class="flex items-center gap-2">
			<!--
			  A plain form, so the rebuild is a POST that works the way every
			  other action on this page does. The button that starts it is not
			  its submit button: it opens the confirmation, and the dialog's
			  confirm submits this through its `form` attribute - which
			  associates across the DOM, so the dialog is free to render
			  wherever it likes.
			-->
			<form
				id="reindex-all"
				method="POST"
				action="?/reindexAll"
				use:enhance={() => {
					rebuilding = true;
					return async ({ update }) => {
						await update();
						rebuilding = false;
					};
				}}
			></form>

			{#if data.documents.length > 0}
				<Button
					variant="quiet"
					onclick={() => (confirmingRebuild = true)}
					disabled={rebuilding}
					title="Rebuild the index for every document in this library"
				>
					{#if rebuilding}
						<Spinner size={14} aria-hidden="true" />
					{:else}
						<RefreshCw size={14} aria-hidden="true" />
					{/if}
					Reindex all
				</Button>
			{/if}

			{#if data.capabilities.ingestion_available}
				<UploadDropzone capabilities={data.capabilities} onupload={upload} />
			{/if}
		</div>
	{/if}
</div>

<!--
  Asked for rather than assumed. Reindexing is not destructive - the current
  index keeps answering until a new generation is built - but it is not
  free either: every document is parsed, embedded and indexed again, which
  on a large library is hours of work and the whole ingestion queue. That is
  worth a deliberate second press.
-->
<AlertDialog.Root bind:open={confirmingRebuild}>
	<AlertDialog.Content>
		<AlertDialog.Header>
			<AlertDialog.Title>
				Reindex {data.documents.length}
				{data.documents.length === 1 ? 'document' : 'documents'}?
			</AlertDialog.Title>
			<AlertDialog.Description>
				Every document in {data.library.name} is read, embedded and indexed again. The library
				keeps answering questions from its current index while that happens, and answers switch
				over one document at a time as each finishes. Nothing is deleted.
			</AlertDialog.Description>
		</AlertDialog.Header>
		<AlertDialog.Footer>
			<AlertDialog.Exit>Cancel</AlertDialog.Exit>
			<AlertDialog.Confirm type="submit" form="reindex-all">Reindex all</AlertDialog.Confirm>
		</AlertDialog.Footer>
	</AlertDialog.Content>
</AlertDialog.Root>

{#if !data.owned}
	<!--
	  Said once, near the title. Everything on this page that a reader
	  cannot do is already absent, and a page where the buttons are simply
	  missing reads as broken unless something explains it.
	-->
	<p class="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
		<Users size={13} aria-hidden="true" />
		Shared with you. You can read these documents and ask questions about them.
	</p>
{/if}

{#if !data.capabilities.ingestion_available && data.owned}
	<!--
	  Warning, not error: nothing has failed, but an upload here would sit in
	  the queue forever and it is better to say so before the drop.
	-->
	<Alert.Root variant="warning" class="mt-6">
		<Alert.Title>No ingestion workers are running</Alert.Title>
		<Alert.Description>
			Uploads would stay queued indefinitely, so documents cannot be added until a worker is
			configured.
		</Alert.Description>
	</Alert.Root>
{/if}

{#if uploadError}
	<Alert.Root variant="error" class="mt-4">
		<Alert.Description>{uploadError}</Alert.Description>
	</Alert.Root>
{/if}

{#if form?.error}
	<Alert.Root variant="error" class="mt-4">
		<Alert.Description>{form.error}</Alert.Description>
	</Alert.Root>
{/if}

{#if form?.rebuilt}
	<!--
	  Both numbers, because they differ for a reason the user should see. A
	  document already being rebuilt is not restarted, so pressing this
	  twice queues nothing the second time - reported as "0 queued" with no
	  explanation, that reads as a failure.
	-->
	<Alert.Root variant="success" class="mt-4">
		<Alert.Description>
			{form.rebuilt.queued}
			{form.rebuilt.queued === 1 ? 'document is' : 'documents are'} being rebuilt.
			{#if form.rebuilt.skipped > 0}
				{form.rebuilt.skipped}
				already {form.rebuilt.skipped === 1 ? 'was' : 'were'}, and {form.rebuilt.skipped === 1
					? 'was'
					: 'were'} left to finish.
			{/if}
		</Alert.Description>
	</Alert.Root>
{/if}

<!--
  Status changes happen without the user acting, so they are announced
  politely rather than interrupting whatever they are reading.
-->
<p class="sr-only" aria-live="polite">{announcement}</p>

{#if data.documents.length === 0 && uploading.length === 0}
	<div class="mt-16 text-center">
		<p class="font-medium">No documents yet</p>
		<p class="mx-auto mt-1.5 max-w-md text-sm text-muted-foreground">
			Add a file above. Once it has been read and indexed it becomes searchable, and answers can
			cite it.
		</p>
	</div>
{:else}
	<!--
	  A table, because these rows are compared down columns: which are ready,
	  which are large, which failed. The header row is what makes that
	  possible, and it is why this is not the list of cards the libraries
	  themselves get.
	-->
	<!--
	  `relative` is load-bearing. The screen-reader labels on the row actions
	  are absolutely positioned, so without a positioned ancestor their
	  containing block is the viewport: they escape the scroll container,
	  land at the far edge of the table, and drag the whole page sideways on
	  a phone. Giving the scroll container a position keeps them inside it.
	-->
	<!--
	  `overscroll-x-contain` so a sideways flick across the table stays in
	  the table. On iOS a horizontal gesture that runs out of content is
	  taken as a back-navigation, which turns reading a wide row into
	  leaving the page.
	-->
	<div class="relative mt-8 overflow-x-auto overscroll-x-contain">
		<!--
		  Fixed layout, so the columns keep the widths set below instead of
		  being sized by whatever happens to be in them. Filenames are long
		  and would otherwise take the whole row on one page and none of it on
		  the next.

		  The minimum width starts at `sm`. Below it, 46rem on a 24rem screen
		  is a table that is technically scrollable and practically unusable -
		  every row read by dragging sideways and back. Narrow screens instead
		  drop the size column and let the rest share the width they have,
		  which is the same table rather than a second one written for phones.
		-->
		<table class="w-full table-fixed border-collapse text-sm sm:min-w-[46rem]">
			<caption class="sr-only">Documents in {data.library.name}</caption>
			<thead>
				<tr class="border-y border-border">
					<!--
					  Size is the one column a narrow screen can do without: it
					  is the only one that answers a question nobody opened this
					  page to ask. Document and Status are why the table exists,
					  and the actions have to stay reachable.
					-->
					{#each [{ label: 'Document', width: 'w-auto' }, { label: 'Size', width: 'hidden w-24 sm:table-cell' }, { label: 'Status', width: 'w-auto sm:w-[22rem]' }] as column (column.label)}
						<th
							scope="col"
							class="{column.width} px-3 py-2 text-left font-mono text-[10px] font-medium
								uppercase tracking-[0.09em] text-muted-foreground"
						>
							{column.label}
						</th>
					{/each}
					<th scope="col" class="w-16 px-3 py-2 sm:w-24">
						<span class="sr-only">Actions</span>
					</th>
				</tr>
			</thead>
			<tbody>
				{#each uploading as name (name)}
					<tr class="border-b border-border text-muted-foreground">
						<td class="px-3 py-2.5 font-mono">{name}</td>
						<td class="hidden px-3 py-2.5 sm:table-cell"></td>
						<td class="px-3 py-2.5">
							<span class="flex items-center gap-2">
								<Spinner size={13} aria-hidden="true" />
								Uploading…
							</span>
						</td>
						<td class="px-3 py-2.5"></td>
					</tr>
				{/each}

				{#each data.documents as document (document.id)}
					<tr class="group border-b border-border transition-colors hover:bg-muted/60">
						<td class="px-3 py-2.5">
							<!--
							  Titled, because the name is truncated and a filename
							  is often told apart by its end, not its start.
							-->
							<a
								href="/libraries/{data.library.id}/documents/{document.id}/content"
								title={document.filename}
								class="block truncate font-mono hover:underline focus-visible:outline-none
									focus-visible:shadow-[var(--focus-ring)]"
							>
								{document.filename}
							</a>
						</td>
						<td
							class="hidden whitespace-nowrap px-3 py-2.5 tabular-nums text-muted-foreground
								sm:table-cell"
						>
							{formatBytes(document.byte_size)}
						</td>
						<td class="px-3 py-2.5">
							<DocumentStatus status={document.status} detail={document.status_detail} />
						</td>
						<td class="px-3 py-2.5">
							<div class="flex items-center justify-end gap-1">
								<form method="POST" action="?/reindex" use:enhance>
									<input type="hidden" name="id" value={document.id} />
									<Button
										type="submit"
										variant="ghost"
										size="icon"
										class="text-muted-foreground"
										title="Reindex"
									>
										<RefreshCw size={14} aria-hidden="true" />
										<span class="sr-only">Reindex {describe(document)}</span>
									</Button>
								</form>
								<form
									method="POST"
									action="?/delete"
									use:enhance={({ cancel }) => {
										if (!confirm(`Delete ${document.filename}?`)) cancel();
									}}
								>
									<input type="hidden" name="id" value={document.id} />
									<Button
										type="submit"
										variant="ghost"
										size="icon"
										class="text-muted-foreground"
										title="Delete"
									>
										<Trash2 size={14} aria-hidden="true" />
										<span class="sr-only">Delete {describe(document)}</span>
									</Button>
								</form>
							</div>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

{#if data.owned}
	<SharePanel
		shares={data.shares}
		error={form?.shareError ?? null}
		shared={form?.shared ?? null}
	/>
{/if}
