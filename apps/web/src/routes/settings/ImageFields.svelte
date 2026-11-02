<script lang="ts">
	import { Checkbox, Input } from '@sivir-ui/svelte';
	import { untrack } from 'svelte';
	import type { ImageCapabilities } from '#lib/api/types.ts';
	import { IMAGE_OPERATIONS } from '#lib/settings/images.ts';

	//: The fields `imageDeclaration` reads, for the add form and the change
	//: form alike. Prefilled from what is declared now, copied rather than
	//: held, so editing the form does not touch the provider it came from.
	//: A caller that wants the form to follow a saved change keys it.
	let { declared = null }: { declared?: ImageCapabilities | null } = $props();

	const initial = untrack(() => declared);
	let operations = $state({
		generate: initial ? initial.operations.includes('generate') : true,
		edit: initial?.operations.includes('edit') ?? false,
		inpaint: initial?.operations.includes('inpaint') ?? false,
	});
	let base64 = $state(initial?.ask_for_base64 ?? false);
</script>

<div class="grid gap-2 sm:grid-cols-[2fr_2fr_1fr]">
	<Input
		name="image_models"
		value={initial?.models.join(', ') ?? ''}
		placeholder="Image models, comma separated"
		aria-label="Image models"
	/>
	<Input
		name="image_sizes"
		value={initial?.sizes.join(', ') ?? '1024x1024'}
		placeholder="Sizes, like 1024x1024"
		aria-label="Image sizes"
	/>
	<Input
		name="image_max"
		type="number"
		min={1}
		max={10}
		value={String(initial?.max_images ?? 1)}
		aria-label="Images per request"
		title="Images per request"
	/>
</div>
<div class="mt-2 flex flex-wrap items-center gap-4 text-xs">
	{#each IMAGE_OPERATIONS as { operation, label } (operation)}
		<Checkbox bind:checked={operations[operation]} {label} />
		<input type="hidden" name="image_{operation}" value={String(operations[operation])} />
	{/each}
	<Checkbox bind:checked={base64} label="Ask for base64" />
	<input type="hidden" name="image_base64" value={String(base64)} />
	<span class="text-muted-foreground">
		Declared, never probed. Ask for base64 if the server returns links: Primer does not follow
		them.
	</span>
</div>
