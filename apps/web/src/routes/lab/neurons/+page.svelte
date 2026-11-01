<script lang="ts">
	import { ScanLine } from '@lucide/svelte';
</script>

<svelte:head>
	<title>Neurons · Lab · Primer</title>
</svelte:head>

<div
	class="flex h-full min-h-0 flex-col items-center justify-center gap-3 rounded-xl border
		border-dashed border-border px-6 text-center"
>
	<ScanLine size={20} aria-hidden="true" class="text-muted-foreground" />
	<h1 class="text-sm font-semibold">Neurons</h1>
	<p class="max-w-md text-xs leading-relaxed text-muted-foreground">
		Run a prompt through Lab's model and read every layer of it. Not here yet.
	</p>
</div>
