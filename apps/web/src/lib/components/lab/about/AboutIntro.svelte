<script lang="ts">
	import { page } from '$app/state';
	import { ArrowRight } from '@lucide/svelte';
	import { Button } from '@sivir-ui/svelte';
	import type { Snippet } from 'svelte';
	import { LAB_TOOLS, type LabTool, runs } from '#lib/lab/tools.ts';

	interface Props {
		tool: LabTool;
		/** The sections below, for a reader who wants one of them. */
		contents: readonly { id: string; title: string }[];
		children: Snippet;
	}

	let { tool, contents, children }: Props = $props();

	let entry = $derived(LAB_TOOLS.find((each) => each.tool === tool) ?? LAB_TOOLS[0]);
	let available = $derived(runs(tool, page.data.capabilities));
</script>

<div class="grid gap-6 md:grid-cols-[minmax(0,1fr)_13rem]">
	<div class="flex flex-col gap-4">
		<p class="max-w-[64ch] text-[15px] leading-7">{@render children()}</p>
		{#if available}
			<div>
				<Button href={entry.href} size="sm" class="gap-1.5">
					Open {entry.label}
					<ArrowRight size={14} aria-hidden="true" />
				</Button>
			</div>
		{:else}
			<p class="text-xs text-muted-foreground">This deployment does not run {entry.label}.</p>
		{/if}
	</div>

	<nav aria-label="On this page" class="border-l border-border pl-4 text-xs">
		<p class="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">On this page</p>
		<ol class="mt-2 flex flex-col gap-1.5">
			{#each contents as section (section.id)}
				<li>
					<a
						href="#{section.id}"
						class="text-muted-foreground hover:text-foreground focus-visible:outline-none
							focus-visible:shadow-[var(--focus-ring)]"
					>
						{section.title}
					</a>
				</li>
			{/each}
		</ol>
	</nav>
</div>
