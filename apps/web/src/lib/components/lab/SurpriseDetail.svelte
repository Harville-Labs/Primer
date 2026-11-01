<script lang="ts">
	import { entropyBasis, entropyOf, SPAN_KINDS, spanText } from '#lib/lab/anomalies.ts';
	import { visibleToken } from '#lib/lab/heatmap.ts';
	import type { Scorer } from '#lib/lab/scorer.svelte.ts';

	/**
	 * The selected token's numbers, and the spans that stand out.
	 *
	 * Every number carries its name and its unit, and an entropy says which
	 * kind it is: from an endpoint it is a lower bound over the top 20, from
	 * Lab it is exact, and the two are not to be compared as equals.
	 */
	let { scorer }: { scorer: Scorer } = $props();

	let score = $derived(scorer.scored?.score ?? null);
	let token = $derived(score?.tokens[scorer.selected] ?? null);
	let entropy = $derived(token ? entropyOf(token) : null);
</script>

<div class="flex flex-col gap-5 px-4 py-4">
	{#if score && token}
		<section aria-labelledby="token-heading" class="flex flex-col gap-2">
			<h2
				id="token-heading"
				class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
			>
				Token {scorer.selected}
			</h2>
			<p class="break-all font-mono text-sm font-medium">{visibleToken(token.token)}</p>
			<dl class="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1.5 text-[11px]">
				<dt class="text-muted-foreground">Surprisal, bits (−log₂ p)</dt>
				<dd class="text-right font-mono tabular-nums">{token.surprisal_bits.toFixed(2)}</dd>
				<dt class="text-muted-foreground">Probability the model gave it</dt>
				<dd class="text-right font-mono tabular-nums">
					{(Math.exp(token.logprob) * 100).toPrecision(3)}%
				</dd>
				<dt class="text-muted-foreground">Bits, {entropyBasis(score)}</dt>
				<dd class="text-right font-mono tabular-nums">{entropy?.toFixed(2) ?? '—'}</dd>
			</dl>
		</section>

		<section aria-labelledby="spans-heading" class="flex flex-col gap-2">
			<h2
				id="spans-heading"
				class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
			>
				Flagged spans
			</h2>
			{#if score.spans.length === 0}
				<p class="text-xs text-muted-foreground">
					Nothing in this text stands out from the rest of it.
				</p>
			{:else}
				<ul class="flex flex-col gap-1.5">
					{#each score.spans as span (`${span.kind}:${span.start}`)}
						{@const kind = SPAN_KINDS[span.kind]}
						{@const current = scorer.selected >= span.start && scorer.selected < span.end}
						<li>
							<button
								type="button"
								onclick={() => scorer.select(span.peak_index)}
								class="flex w-full flex-col gap-1 rounded-lg border p-2.5 text-left text-xs
									transition-colors focus-visible:outline-none
									focus-visible:shadow-[var(--focus-ring)]
									{current ? 'border-foreground/40 bg-card' : 'border-border hover:bg-card'}"
							>
								<span class="flex items-baseline justify-between gap-2">
									<span class="font-medium">{kind.label}</span>
									<span class="font-mono text-[10.5px] tabular-nums text-muted-foreground">
										tokens {span.start}–{span.end - 1}
									</span>
								</span>
								<span class="truncate font-mono text-[11px]">
									{visibleToken(spanText(score, span))}
								</span>
								<span class="text-[10.5px] text-muted-foreground">
									Peak {kind.measures} {span.peak_z.toFixed(1)} robust z: deviations from this
									text's median, at token {span.peak_index}.
								</span>
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</section>
	{:else}
		<p class="text-xs leading-relaxed text-muted-foreground">
			Score something to see each token's surprisal, the model's uncertainty where it wrote it,
			and the stretches that stand out.
		</p>
	{/if}
</div>
