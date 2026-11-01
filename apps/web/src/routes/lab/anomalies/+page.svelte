<script lang="ts">
	import { Activity } from '@lucide/svelte';
</script>

<svelte:head>
	<title>Anomalies · Lab · Primer</title>
</svelte:head>

<!--
  A shell until scoring lands, so the section's shape is settled now and the
  link has somewhere honest to go. This is for evaluating a model - where
  its answers are surprising, token by token - not for anything in a library
  or a chat.
-->
<div
	class="flex h-full min-h-0 flex-col items-center justify-center gap-3 rounded-xl border
		border-dashed border-border px-6 text-center"
>
	<Activity size={20} aria-hidden="true" class="text-muted-foreground" />
	<h1 class="text-sm font-semibold">Anomalies</h1>
	<p class="max-w-md text-xs leading-relaxed text-muted-foreground">
		Score a model's own output for the tokens it found least likely, to see where an evaluation
		run went somewhere the model did not expect. Not here yet.
	</p>
</div>
