<script lang="ts" module>
	export interface Reference {
		authors: string;
		year: number;
		title: string;
		href?: string;
		/** What the page takes from it. */
		note: string;
	}
</script>

<script lang="ts">
	let { references }: { references: readonly Reference[] } = $props();
</script>

<ol class="flex max-w-[72ch] flex-col gap-3 text-sm">
	{#each references as reference (reference.title)}
		<li class="leading-relaxed">
			<span class="text-foreground/90">{reference.authors} ({reference.year}).</span>
			{#if reference.href}
				<a
					href={reference.href}
					rel="noreferrer"
					target="_blank"
					class="font-medium text-[var(--color-lab-positive)] underline-offset-2 hover:underline"
				>
					{reference.title}
				</a>
			{:else}
				<em>{reference.title}</em>
			{/if}
			<span class="block text-xs text-muted-foreground">{reference.note}</span>
		</li>
	{/each}
</ol>
