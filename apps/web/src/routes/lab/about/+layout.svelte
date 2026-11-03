<script lang="ts">
	import { page } from '$app/state';
	import type { Snippet } from 'svelte';
	import { LAB_TOOLS } from '#lib/lab/tools.ts';

	let { children }: { children: Snippet } = $props();
</script>

<!--
  Every explainer is listed even where this deployment cannot run its tool:
  how Scan works is worth reading before an operator decides to enable it.
  Each page offers to open its tool only where the tool runs.
-->
<div class="mx-auto w-full max-w-4xl pb-16">
	<p class="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Lab</p>
	<h1 class="mt-1 text-xl font-semibold tracking-[-0.02em]">How Lab works</h1>
	<p class="mt-1 max-w-2xl text-sm text-muted-foreground">
		What each tool measures, how Primer measures it, and the research it rests on.
	</p>

	<nav aria-label="Explainers" class="mt-5 flex gap-1 border-b border-border">
		{#each LAB_TOOLS as entry (entry.tool)}
			{@const current = page.url.pathname === entry.about}
			<a
				href={entry.about}
				aria-current={current ? 'page' : undefined}
				class="-mb-px border-b-2 px-3 py-2 text-sm transition-colors focus-visible:outline-none
					focus-visible:shadow-[var(--focus-ring)]
					{current
					? 'border-[var(--color-lab-positive)] font-medium text-foreground'
					: 'border-transparent text-muted-foreground hover:text-foreground'}"
			>
				{entry.label}
			</a>
		{/each}
	</nav>

	<div class="mt-8">
		{@render children()}
	</div>
</div>
