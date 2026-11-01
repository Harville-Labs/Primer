<script lang="ts">
	import { TriangleAlert } from '@lucide/svelte';
	import type { Probe } from '#lib/api/types.ts';

	/**
	 * How well a probe did, beside what it would have done by guessing.
	 *
	 * The comparison is the point: a probe on a few prompts in thousands of
	 * dimensions fits its training prompts whatever they are, so an accuracy
	 * read without chance beside it says nothing.
	 */
	let { probe }: { probe: Probe } = $props();

	const percent = (share: number) => `${Math.round(share * 100)}%`;
</script>

<span class="font-mono text-[11px] tabular-nums text-muted-foreground">
	L{probe.layer} · {percent(probe.accuracy)} held out, against {percent(probe.chance)} by chance ·
	{probe.positive} vs {probe.negative}
</span>
{#if !probe.beats_chance}
	<span class="flex items-start gap-1.5 text-[11px] text-foreground">
		<TriangleAlert size={12} aria-hidden="true" class="mt-px shrink-0 text-warning" />
		Not better than chance on prompts it did not see. Its readings mean nothing yet: give it more
		prompts, or sets that differ more plainly.
	</span>
{/if}
