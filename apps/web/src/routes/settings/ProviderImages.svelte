<script lang="ts">
	import { enhance } from '$app/forms';
	import { Image } from '@lucide/svelte';
	import { Badge, Button } from '@sivir-ui/svelte';
	import type { ProviderSummary } from '#lib/api/types.ts';
	import ImageFields from './ImageFields.svelte';

	//: A provider's image declaration, and for one added here the form that
	//: changes it. The deployment's own is declared in the chart, so it is
	//: only described.
	let { provider }: { provider: ProviderSummary } = $props();
</script>

{#if provider.source === 'deployment' || provider.images?.api === 'comfyui'}
	<!--
	  A ComfyUI provider's templates are not editable here yet, so it gets no
	  form: saving the OpenAI fields would replace its templates with nothing.
	-->
	{#if provider.images}
		<Badge variant="secondary" title={provider.images.models.join(', ')}>
			<Image size={12} aria-hidden="true" />
			Images: {provider.images.operations.join(', ')}
		</Badge>
	{/if}
{:else}
	<details class="w-full text-xs">
		<summary class="cursor-pointer text-muted-foreground">
			{#if provider.images}
				Images: {provider.images.models.join(', ')} ({provider.images.operations.join(', ')})
			{:else}
				Declare images
			{/if}
		</summary>
		<form method="POST" action="?/images" use:enhance class="mt-2">
			<input type="hidden" name="id" value={provider.id} />
			{#key provider.images}
				<ImageFields declared={provider.images} />
			{/key}
			<div class="mt-2 flex gap-2">
				<Button type="submit" size="sm">Save images</Button>
				{#if provider.images && provider.serves_chat}
					<!--
					  Not offered for an image-only provider: without images it
					  would serve nothing, and Chat refuses that anyway.
					-->
					<Button type="submit" name="withdraw" value="true" variant="ghost" size="sm">
						Withdraw
					</Button>
				{/if}
			</div>
		</form>
	</details>
{/if}
