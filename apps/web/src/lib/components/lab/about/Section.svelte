<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		/** The fragment the page's contents link to. */
		id: string;
		title: string;
		children: Snippet;
	}

	let { id, title, children }: Props = $props();
</script>

<!--
  Prose and figures share one column, but a line of prose is held to a
  readable measure while a figure takes the full width: a figure squeezed
  to seventy characters is too small to read its labels.
-->
<section {id} aria-labelledby="{id}-title" class="scroll-mt-6">
	<h2 id="{id}-title" class="text-base font-semibold tracking-[-0.01em]">{title}</h2>
	<div
		class="mt-3 flex flex-col gap-4 text-sm leading-7 text-foreground/90 [&>p]:max-w-[72ch]
			[&>ul]:max-w-[72ch] [&>ul]:list-disc [&>ul]:space-y-1.5 [&>ul]:pl-5
			[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-[12px]"
	>
		{@render children()}
	</div>
</section>
