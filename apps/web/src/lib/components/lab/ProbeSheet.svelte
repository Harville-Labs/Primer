<script lang="ts">
	import { Button, Sheet, Spinner } from '@sivir-ui/svelte';
	import ProbeVerdict from '#lib/components/lab/ProbeVerdict.svelte';
	import PromptSets from '#lib/components/lab/PromptSets.svelte';
	import { promptLines } from '#lib/lab/prompts.ts';
	import type { Probes, Scorer } from '#lib/lab/scorer.svelte.ts';

	/**
	 * Training a probe, and picking one to read beside each scored token.
	 */
	interface Props {
		open: boolean;
		probes: Probes;
		scorer: Scorer;
		numLayers: number;
	}

	let { open = $bindable(), probes, scorer, numLayers }: Props = $props();

	let label = $state('');
	// Derived so a new model resets it, and still free to be moved.
	let layer = $derived(Math.floor(numLayers / 2));
	let positive = $state('');
	let negative = $state('');

	let positives = $derived(promptLines(positive));
	let negatives = $derived(promptLines(negative));
	let trainable = $derived(
		Boolean(label.trim()) && positives.length > 0 && negatives.length > 0 && !probes.creating
	);

	function use(id: string) {
		scorer.probeId = id;
		open = false;
	}

	async function train() {
		const probe = await probes.create({
			label: label.trim(),
			layer,
			positive: positives,
			negative: negatives,
			// Read as plain text, not through the chat template: the probe is
			// applied to the tokens of a scored text, and a probe trained on
			// templated prompts would be reading a different stream there.
			chat: false,
		});
		if (probe) {
			label = '';
			scorer.probeId = probe.id;
		}
	}

	const heading = 'font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground';
</script>

<Sheet.Root bind:open>
	<Sheet.Content side="left" class="flex w-[26rem] max-w-full flex-col gap-6 overflow-y-auto p-5">
		<Sheet.Header>
			<Sheet.Title>Probes</Sheet.Title>
			<Sheet.Description>
				A linear probe learns to tell two sets of prompts apart from the residual stream at one
				layer. Scoring with Lab then reads it at every token.
			</Sheet.Description>
		</Sheet.Header>

		<section aria-labelledby="train-heading" class="flex flex-col gap-3">
			<h3 id="train-heading" class={heading}>Train on two sets of prompts</h3>
			<p class="text-xs leading-relaxed text-muted-foreground">
				Trained at each prompt's last token, and measured on prompts held out of training. At least
				four a set, so some can be held out; a few dozen make the measurement worth reading.
			</p>
			<PromptSets
				bind:label
				bind:layer
				bind:positive
				bind:negative
				{numLayers}
				labelName="Probe label"
				labelPlaceholder="formal"
				names={['Reads as', 'Does not']}
				placeholders={['Dear Sir or Madam,\nI am writing to inform you', 'hey,\nso basically']}
			/>
			<Button onclick={train} disabled={!trainable} class="gap-1.5">
				{#if probes.creating}<Spinner size={13} aria-hidden="true" />{/if}
				Train and use
			</Button>
			{#if probes.failure}
				<p role="alert" class="text-xs">
					<span class="font-medium text-error">{probes.failure.title}.</span>
					{probes.failure.detail}
				</p>
			{/if}
		</section>

		{#if probes.items.length > 0}
			<section aria-labelledby="probes-heading" class="flex flex-col gap-2">
				<h3 id="probes-heading" class={heading}>Your probes</h3>
				<ul class="flex flex-col gap-1">
					{#each probes.items as probe (probe.id)}
						<li>
							<button
								type="button"
								onclick={() => use(probe.id)}
								aria-pressed={scorer.probeId === probe.id}
								class="flex w-full flex-col gap-1 rounded-md border px-3 py-2 text-left
									transition-colors focus-visible:outline-none
									focus-visible:shadow-[var(--focus-ring)]
									{scorer.probeId === probe.id
									? 'border-foreground/40 bg-card'
									: 'border-border hover:bg-card'}"
							>
								<span class="truncate text-sm font-medium">{probe.label}</span>
								<ProbeVerdict {probe} />
							</button>
						</li>
					{/each}
				</ul>
				<p class="text-[11px] text-muted-foreground">Kept until the model changes.</p>
			</section>
		{/if}
	</Sheet.Content>
</Sheet.Root>
