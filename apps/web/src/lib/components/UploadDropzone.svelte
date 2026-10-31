<script lang="ts">
	import { Alert, Button } from '@sivir-ui/svelte';
	import type { DeploymentCapabilities } from '#lib/api/types.ts';
	import { describeAccepted, rejectionFor } from '#lib/upload.ts';

	interface Props {
		capabilities: DeploymentCapabilities;
		onupload?: (files: File[]) => void;
	}

	let { capabilities, onupload }: Props = $props();

	let dragging = $state(false);
	let errors = $state<string[]>([]);
	let input = $state<HTMLInputElement>();

	function accept(files: FileList | null) {
		if (!files || files.length === 0) return;
		const accepted: File[] = [];
		const rejected: string[] = [];

		for (const file of files) {
			const rejection = rejectionFor(file, capabilities);
			if (rejection) rejected.push(rejection.message);
			else accepted.push(file);
		}

		// Both are reported. Dropping five files and having two rejected
		// silently would look like the upload half-worked.
		errors = rejected;
		if (accepted.length > 0) onupload?.(accepted);
	}
</script>

<!--
  A button, not just a drop target. Dragging is a mouse gesture with no
  keyboard equivalent, so the same control opens a file picker on Enter or
  Space and the whole flow works without a pointer.
-->
<div
	class="flex items-center gap-3 rounded-lg border border-dashed px-3 py-2 transition-colors
		{dragging ? 'border-primary bg-primary/5' : 'border-border'}"
	ondragover={(event) => {
		event.preventDefault();
		dragging = true;
	}}
	ondragleave={() => (dragging = false)}
	ondrop={(event) => {
		event.preventDefault();
		dragging = false;
		accept(event.dataTransfer?.files ?? null);
	}}
	role="presentation"
>
	<Button variant="secondary" size="sm" onclick={() => input?.click()} aria-describedby="upload-hint">
		Add documents
	</Button>

	<p id="upload-hint" class="text-xs text-muted-foreground">
		or drop {describeAccepted(capabilities)} files here
	</p>

	<input
		bind:this={input}
		type="file"
		multiple
		class="sr-only"
		accept={capabilities.supported_extensions.join(',')}
		data-testid="file-input"
		onchange={(event) => {
			accept(event.currentTarget.files);
			// Reset, so choosing the same file twice fires a change event
			// both times - re-uploading after a failure is a real case.
			event.currentTarget.value = '';
		}}
	/>
</div>

<!--
  One alert listing every rejection, not one per file. Alert.Root carries
  role="alert", which is an assertive live region by definition, so a
  screen reader announces this the moment it appears - a user who dropped a
  file somewhere else on the page would otherwise hear nothing at all.
-->
{#if errors.length > 0}
	<Alert.Root variant="error" class="mt-3">
		<Alert.Title>
			{errors.length === 1 ? 'That file was not accepted' : 'Some files were not accepted'}
		</Alert.Title>
		{#each errors as message (message)}
			<Alert.Description>{message}</Alert.Description>
		{/each}
	</Alert.Root>
{/if}
