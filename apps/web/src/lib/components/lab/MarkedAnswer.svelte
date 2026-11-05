<script lang="ts">
	import type { MarkedToken } from '#lib/lab/generation.ts';
	import { visibleToken } from '#lib/lab/heatmap.ts';

	/** One of a comparison's two answers, each token marked against the other's. */
	interface Props {
		title: string;
		tokens: MarkedToken[];
		/** Whether this answer is the one being written now, which draws a caret. */
		running: boolean;
	}

	let { title, tokens, running }: Props = $props();

	// The first divergent token is outlined in both answers, so the eye can
	// go straight across; what follows is tinted, because from there the two
	// are different texts and a token that happens to match is not agreement.
	const CLASSES: Record<MarkedToken['agreement'], string> = {
		same: '',
		first:
			'rounded-[3px] bg-[color-mix(in_oklab,var(--color-lab-positive)_28%,transparent)] ring-1 ring-[var(--color-lab-positive)]',
		after: 'bg-[color-mix(in_oklab,var(--color-lab-positive)_10%,transparent)]',
	};
</script>

<section aria-label={title} class="flex min-w-0 flex-col gap-2">
	<h2 class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
		{title}
	</h2>
	<p
		class="min-h-24 whitespace-pre-wrap break-words rounded-lg border border-border bg-card p-4
			font-mono text-[13px] leading-relaxed"
	>
		{#each tokens as each, index (index)}<span
				class="{CLASSES[each.agreement]} {each.token.special ? 'text-muted-foreground' : ''}"
				title={each.agreement === 'first' ? `First difference, token ${index}` : undefined}
				>{each.token.special ? visibleToken(each.token.text) : each.token.text}</span
			>{/each}{#if running}<span
				aria-hidden="true"
				class="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-muted-foreground align-middle"
			></span>{/if}
	</p>
</section>
