<script lang="ts">
	import type { LucideIcon } from '@lucide/svelte';
	import type { Snippet } from 'svelte';

	interface Props {
		icon: LucideIcon;
		title: string;
		/** Where its explainer is, offered beside the description. */
		about?: string;
		children: Snippet;
	}

	let { icon: Glyph, title, about, children }: Props = $props();
</script>

<div class="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
	<span
		aria-hidden="true"
		class="grid size-12 place-items-center rounded-xl border border-border"
		style:background-image="linear-gradient(135deg, var(--color-card), color-mix(in oklab, var(--color-lab-positive) 35%, var(--color-card)))"
	>
		<Glyph size={18} class="text-[var(--color-lab-positive)]" />
	</span>
	<p class="text-sm font-medium">{title}</p>
	<p class="max-w-sm text-xs leading-relaxed text-muted-foreground">{@render children()}</p>
	{#if about}
		<a
			href={about}
			class="text-xs font-medium text-[var(--color-lab-positive)] underline-offset-2 hover:underline
				focus-visible:outline-none focus-visible:shadow-[var(--focus-ring)]"
		>
			How it works
		</a>
	{/if}
</div>
