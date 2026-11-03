<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		number: number;
		title: string;
		/** One or two sentences: what to look at, and whether the values are real. */
		caption: string;
		children: Snippet;
	}

	let { number, title, caption, children }: Props = $props();
</script>

<figure class="overflow-hidden rounded-xl border border-border bg-card">
	<div
		class="border-b border-border bg-muted/30 px-4 py-2 font-mono text-[11px] tracking-wide
			text-muted-foreground"
	>
		Figure {number} · {title}
	</div>
	<div class="px-4 py-5">
		{@render children()}
	</div>
	<figcaption class="border-t border-border px-4 py-3 text-xs leading-relaxed text-muted-foreground">
		{caption}
	</figcaption>
</figure>
