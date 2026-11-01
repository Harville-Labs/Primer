<script lang="ts">
	import { Plus } from '@lucide/svelte';
	import { Button, Input, Select, Sheet, Slider, Spinner, Textarea } from '@sivir-ui/svelte';
	import type { Edits, Steering } from '#lib/lab/edits.svelte.ts';

	/**
	 * Steering: a direction in the residual stream, made from two sets of
	 * prompts or taken from an SAE feature, added as an edit.
	 */
	interface Props {
		open: boolean;
		edits: Edits;
		steering: Steering;
		numLayers: number;
		//: The layers the model's SAEs read; empty when it has none ready.
		saeLayers: number[];
		saeWidth: number | null;
		//: Whether the prompts are read through the chat template, as the inspector's are.
		chat: boolean;
	}

	let {
		open = $bindable(),
		edits,
		steering,
		numLayers,
		saeLayers,
		saeWidth,
		chat,
	}: Props = $props();

	let label = $state('');
	// Derived so a new model resets them, and still free to be moved.
	let layer = $derived(Math.floor(numLayers / 2));
	let positive = $state('');
	let negative = $state('');
	let featureLayer = $derived(saeLayers[0]?.toString() ?? '');
	let feature = $state('');

	// One prompt a line, so a list pasted from anywhere reads as one.
	const lines = (text: string) =>
		text
			.split('\n')
			.map((line) => line.trim())
			.filter(Boolean);

	let positives = $derived(lines(positive));
	let negatives = $derived(lines(negative));
	let makeable = $derived(
		Boolean(label.trim()) && positives.length > 0 && negatives.length > 0 && !steering.creating
	);
	let featureIndex = $derived(feature.trim() === '' ? null : Number(feature));
	let featureValid = $derived(
		featureIndex !== null &&
			Number.isInteger(featureIndex) &&
			featureIndex >= 0 &&
			(saeWidth === null || featureIndex < saeWidth)
	);

	function steer(vectorId: string, at: number) {
		edits.add({ site: 'resid_post', layer: at, op: 'add', vector_id: vectorId });
		open = false;
	}

	async function make() {
		const vector = await steering.create({
			label: label.trim(),
			layer,
			positive: positives,
			negative: negatives,
			chat,
		});
		if (vector) {
			label = '';
			steer(vector.id, vector.layer);
		}
	}

	function steerByFeature() {
		if (featureIndex === null) return;
		edits.add({ site: 'sae_feature', layer: Number(featureLayer), index: featureIndex, op: 'add' });
		open = false;
	}

	const number = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 3 });
	const heading = 'font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground';
</script>

<Sheet.Root bind:open>
	<Sheet.Content side="right" class="flex w-[26rem] max-w-full flex-col gap-6 overflow-y-auto p-5">
		<Sheet.Header>
			<Sheet.Title>Steer</Sheet.Title>
			<Sheet.Description>
				Push the residual stream along a direction at every position. Both answers are generated
				again, one with the push and one without.
			</Sheet.Description>
		</Sheet.Header>

		<section aria-labelledby="make-heading" class="flex flex-col gap-3">
			<h3 id="make-heading" class={heading}>From two sets of prompts</h3>
			<p class="text-xs leading-relaxed text-muted-foreground">
				The direction is the mean of the first set's residual minus the second's, at each prompt's
				last token. Pairs that differ only in what you want to steer work best.
			</p>
			<Input bind:value={label} placeholder="formal" aria-label="Vector label" maxlength={80} />
			<Slider
				bind:value={layer}
				min={0}
				max={numLayers - 1}
				step={1}
				label="Layer"
				format={(value) => `L${value}`}
			/>
			<Textarea
				bind:value={positive}
				label="Towards"
				description="One prompt a line. {positives.length} so far."
				placeholder={'Dear Sir or Madam,\nI am writing to inform you'}
				rows={3}
				autoresize
			/>
			<Textarea
				bind:value={negative}
				label="Away from"
				description="One prompt a line. {negatives.length} so far."
				placeholder={'hey,\nso basically'}
				rows={3}
				autoresize
			/>
			<Button onclick={make} disabled={!makeable} class="gap-1.5">
				{#if steering.creating}<Spinner size={13} aria-hidden="true" />{/if}
				Make and add
			</Button>
			{#if steering.failure}
				<p role="alert" class="text-xs">
					<span class="font-medium text-error">{steering.failure.title}.</span>
					{steering.failure.detail}
				</p>
			{/if}
		</section>

		{#if steering.vectors.length > 0}
			<section aria-labelledby="vectors-heading" class="flex flex-col gap-2">
				<h3 id="vectors-heading" class={heading}>Your vectors</h3>
				<ul class="flex flex-col gap-1">
					{#each steering.vectors as vector (vector.id)}
						<li class="flex items-center gap-3 rounded-md border border-border bg-card px-3 py-2">
							<div class="min-w-0 flex-1">
								<p class="truncate text-sm font-medium">{vector.label}</p>
								<p class="font-mono text-[11px] tabular-nums text-muted-foreground">
									L{vector.layer} · ‖v‖ {number.format(vector.norm)} · {vector.positive} vs
									{vector.negative}
								</p>
							</div>
							<Button
								variant="ghost"
								size="icon"
								class="size-7"
								onclick={() => steer(vector.id, vector.layer)}
							>
								<Plus size={13} aria-hidden="true" />
								<span class="sr-only">Add {vector.label} as an edit</span>
							</Button>
						</li>
					{/each}
				</ul>
				<p class="text-[11px] text-muted-foreground">Kept until the model changes.</p>
			</section>
		{/if}

		<section aria-labelledby="feature-heading" class="flex flex-col gap-3">
			<h3 id="feature-heading" class={heading}>Along a feature</h3>
			{#if saeLayers.length === 0}
				<p class="text-xs text-muted-foreground">
					This model has no SAEs ready, so it has no feature directions.
				</p>
			{:else}
				<p class="text-xs leading-relaxed text-muted-foreground">
					Adds to a feature's activation, which writes its decoder direction into the stream.
				</p>
				<div class="flex items-end gap-2">
					<Select.Root bind:value={featureLayer}>
						<Select.Trigger class="h-9 w-24 font-mono text-xs" aria-label="SAE layer">
							<!-- As in the edits panel: Select.Value is blank until the menu opens. -->
							<span>L{featureLayer}</span>
						</Select.Trigger>
						<Select.Content>
							{#each saeLayers as each (each)}
								<Select.Item value={each.toString()} class="font-mono text-xs">L{each}</Select.Item>
							{/each}
						</Select.Content>
					</Select.Root>
					<Input
						bind:value={feature}
						inputmode="numeric"
						placeholder={saeWidth ? `0 to ${saeWidth - 1}` : 'Feature'}
						aria-label="Feature index"
						class="h-9 flex-1 font-mono text-xs"
					/>
					<Button variant="secondary" onclick={steerByFeature} disabled={!featureValid}>Add</Button>
				</div>
			{/if}
		</section>
	</Sheet.Content>
</Sheet.Root>
