<script lang="ts">
	import { Plus } from '@lucide/svelte';
	import { Button, Select } from '@sivir-ui/svelte';
	import type { Probes, Scorer } from '#lib/lab/scorer.svelte.ts';

	/**
	 * Which of Lab's detectors to read beside the score: a layer to measure
	 * each token's distance from ordinary text at, and a probe.
	 */
	interface Props {
		scorer: Scorer;
		probes: Probes;
		numLayers: number;
		ontrain: () => void;
	}

	let { scorer, probes, numLayers, ontrain }: Props = $props();

	const OFF = 'off';
	let layers = $derived(Array.from({ length: numLayers }, (_, layer) => layer));
	let probe = $derived(probes.find(scorer.probeId));
</script>

<section aria-labelledby="detectors-heading" class="flex flex-col gap-3">
	<h2
		id="detectors-heading"
		class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
	>
		Detectors
	</h2>

	<div class="flex flex-col gap-1.5">
		<span aria-hidden="true" class="text-xs font-medium">Distance from ordinary text</span>
		<Select.Root
			value={scorer.outlierLayer?.toString() ?? OFF}
			onValueChange={(value) => {
				scorer.outlierLayer = value === OFF ? null : Number(value);
			}}
		>
			<Select.Trigger
				class="h-9 w-full font-mono text-xs"
				disabled={scorer.running}
				aria-label="Layer to measure distance after"
			>
				<!-- As elsewhere in Lab: Select.Value is blank until the menu opens. -->
				<span>{scorer.outlierLayer === null ? 'Off' : `After L${scorer.outlierLayer}`}</span>
			</Select.Trigger>
			<Select.Content>
				<Select.Item value={OFF} class="text-xs">Off</Select.Item>
				{#each layers as layer (layer)}
					<Select.Item value={layer.toString()} class="font-mono text-xs">After L{layer}</Select.Item>
				{/each}
			</Select.Content>
		</Select.Root>
		<p class="text-[11px] leading-relaxed text-muted-foreground">
			How far each token's residual is from those of plain reference paragraphs. The first score
			at a layer takes longer while the reference is read.
			{#if scorer.effectiveMode === 'generate'}
				The reference is plain text and an answer comes after the chat template, so expect an
				answer to sit further out than the same words given as text.
			{/if}
		</p>
	</div>

	<div class="flex flex-col gap-1.5">
		<span aria-hidden="true" class="text-xs font-medium">Probe</span>
		<div class="flex gap-2">
			<Select.Root
				value={scorer.probeId ?? OFF}
				onValueChange={(value) => {
					scorer.probeId = value === OFF ? null : value;
				}}
			>
				<Select.Trigger
					class="h-9 min-w-0 flex-1 text-xs"
					disabled={scorer.running}
					aria-label="Probe to read"
				>
					<span class="truncate">{probe?.label ?? 'Off'}</span>
				</Select.Trigger>
				<Select.Content>
					<Select.Item value={OFF} class="text-xs">Off</Select.Item>
					{#each probes.items as each (each.id)}
						<Select.Item value={each.id} class="text-xs">{each.label} · L{each.layer}</Select.Item>
					{/each}
				</Select.Content>
			</Select.Root>
			<Button variant="secondary" size="icon" class="size-9" onclick={ontrain}>
				<Plus size={14} aria-hidden="true" />
				<span class="sr-only">Train a probe</span>
			</Button>
		</div>
		<p class="text-[11px] leading-relaxed text-muted-foreground">
			{#if probes.items.length === 0}
				Train one on two sets of prompts to read, at each token, which the text is more like.
			{:else}
				Trained on whole prompts' last tokens, so its reading at a single token is a hint, not a
				measurement.
			{/if}
		</p>
	</div>
</section>
