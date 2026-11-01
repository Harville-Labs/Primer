<script lang="ts">
	import { GitCompareArrows, TriangleAlert } from '@lucide/svelte';
	import { Spinner } from '@sivir-ui/svelte';
	import type { Generator } from '#lib/lab/edits.svelte.ts';
	import { divergence, type MarkedToken, markTokens } from '#lib/lab/generation.ts';
	import { visibleToken } from '#lib/lab/heatmap.ts';

	/** The model's own answer beside the edited one, and where they part. */
	interface Props {
		generator: Generator;
	}

	let { generator }: Props = $props();

	let view = $derived(generator.view);
	let marked = $derived(markTokens(view));
	let at = $derived(divergence(view));
	let target = $derived(view.completed?.target ?? null);
	let started = $derived(view.baseline.length > 0 || view.edited.length > 0);

	const percent = (value: number) => `${(value * 100).toFixed(value < 0.001 ? 3 : 1)}%`;
	const signed = new Intl.NumberFormat(undefined, {
		maximumFractionDigits: 2,
		signDisplay: 'exceptZero',
	});
	const plain = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

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

{#snippet answer(title: string, tokens: MarkedToken[], running: boolean)}
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
{/snippet}

<div class="flex h-full flex-col gap-4 overflow-y-auto p-4">
	{#if view.failure}
		<div role="alert" class="flex gap-2 rounded-lg border border-error/40 bg-error/5 p-3 text-xs">
			<TriangleAlert size={14} aria-hidden="true" class="mt-px shrink-0 text-error" />
			<p><span class="font-medium text-error">{view.failure.title}.</span> {view.failure.detail}</p>
		</div>
	{/if}

	{#if !started && !generator.running && !view.failure}
		<div class="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
			<span
				aria-hidden="true"
				class="grid size-12 place-items-center rounded-xl border border-border"
				style:background-image="linear-gradient(135deg, var(--color-card), color-mix(in oklab, var(--color-lab-positive) 35%, var(--color-card)))"
			>
				<GitCompareArrows size={18} class="text-[var(--color-lab-positive)]" />
			</span>
			<p class="text-sm font-medium">See what an edit changes</p>
			<p class="max-w-sm text-xs leading-relaxed text-muted-foreground">
				Generate both to write the prompt's answer twice, greedily: once as the model would, once
				with the edits made at every step. The first token where they part is marked in each.
			</p>
		</div>
	{:else}
		<div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs" role="status">
			{#if generator.running && view.ahead}
				<span class="flex items-center gap-2 text-muted-foreground">
					<Spinner size={12} aria-hidden="true" />
					Waiting behind {view.ahead}
					{view.ahead === 1 ? 'run' : 'runs'}…
				</span>
			{:else if generator.running}
				<span class="flex items-center gap-2 text-muted-foreground">
					<Spinner size={12} aria-hidden="true" />
					{view.edited.length === 0 ? 'Writing the model’s own answer…' : 'Writing the edited answer…'}
				</span>
			{/if}
			{#if at !== null}
				<span class="font-medium">Parts at token {at}</span>
			{:else if view.completed}
				<span class="text-muted-foreground">The edits did not change the answer.</span>
			{/if}
		</div>

		{#if target}
			{@const change = target.edited_logit - target.baseline_logit}
			<section
				aria-label="Watched token"
				class="flex flex-wrap items-baseline gap-x-6 gap-y-2 rounded-lg border border-border bg-card px-4 py-3"
			>
				<p class="text-xs text-muted-foreground">
					<span class="font-mono text-foreground">{visibleToken(target.token.text)}</span> at the first
					answer token
				</p>
				<p class="font-mono text-xs tabular-nums">
					logit {plain.format(target.baseline_logit)} → {plain.format(target.edited_logit)}
					<span
						class="ml-1 font-semibold"
						style:color={change < 0 ? 'var(--color-lab-negative)' : 'var(--color-lab-positive)'}
						>({signed.format(change)})</span
					>
				</p>
				<p class="font-mono text-xs tabular-nums">
					p {percent(target.baseline_probability)} → {percent(target.edited_probability)}
				</p>
			</section>
		{/if}

		<div class="grid gap-4 xl:grid-cols-2">
			{@render answer(
				'The model’s own',
				marked.baseline,
				generator.running && view.edited.length === 0 && !view.ahead
			)}
			{@render answer('Edited', marked.edited, generator.running && view.edited.length > 0)}
		</div>
	{/if}
</div>
