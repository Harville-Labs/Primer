<script lang="ts">
	import { Alert, Button } from '@sivir-ui/svelte';
	import type { ChatUploads } from '#lib/chat-uploads.svelte.ts';

	let {
		uploads,
		ondiscard
	}: {
		uploads: ChatUploads;
		/** Remove the library a drop created. The page owns which library is linked. */
		ondiscard: () => void;
	} = $props();
</script>

{#if uploads.success}
	<Alert.Root variant="success" class="mt-3">
		<Alert.Description>{uploads.success}</Alert.Description>
	</Alert.Root>
{/if}

{#if uploads.error}
	<Alert.Root variant="error" class="mt-3">
		<Alert.Description>{uploads.error}</Alert.Description>
	</Alert.Root>
{/if}

{#if uploads.offered}
	{@const offered = uploads.offered}
	<!--
	  Asked, not assumed. Dropping a file into a conversation with nowhere
	  to put it had to create somewhere, and a library the user never
	  chose should not quietly become permanent. Keeping is the default
	  action because the document is already in it and already searchable.
	-->
	<div
		class="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-border
			bg-card p-3 text-sm shadow-[var(--elevation-1)]"
	>
		<span class="min-w-0 flex-1">
			Started a library called
			<strong class="font-medium">{offered.name}</strong>
			to hold
			{offered.documents === 1 ? 'this file' : `these ${offered.documents} files`}. Keep it?
		</span>
		<div class="flex items-center gap-2">
			<Button size="sm" onclick={() => uploads.keep()}>Keep</Button>
			<Button size="sm" variant="ghost" onclick={ondiscard}>Discard</Button>
		</div>
	</div>
{/if}
