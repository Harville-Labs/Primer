<script lang="ts">
	import { Compass, Play, Square } from '@lucide/svelte';
	import { Button, Input, ReorderList, Select, Slider } from '@sivir-ui/svelte';
	import type { DraftEdit, Edits, Generator, Steering } from '#lib/lab/edits.svelte.ts';
	import { editEffect, editTarget } from '#lib/lab/generation.ts';
	import { featureLabel } from '#lib/lab/heatmap.ts';
	import type { Inspector } from '#lib/lab/inspector.svelte.ts';
	import EditEditor from './EditEditor.svelte';

	/** The edits to make, the one being set, and the run that compares them. */
	interface Props {
		inspector: Inspector;
		edits: Edits;
		generator: Generator;
		steering: Steering;
		//: The deployment's limit on an answer's length.
		maxNewTokens: number;
		onsteer: () => void;
		ongenerate: () => void;
	}

	let { inspector, edits, generator, steering, maxNewTokens, onsteer, ongenerate }: Props =
		$props();

	// Follows the limit until it is moved, so a smaller limit cannot leave it past the end.
	let length = $derived(Math.min(24, maxNewTokens));
	let target = $state('');

	let current = $derived(edits.current);
	let runnable = $derived(Boolean(inspector.prompt.trim()) && !inspector.running);

	function name(edit: DraftEdit): string {
		return editTarget(edit, label(edit));
	}

	// A feature's label comes from the reading on screen, which carries
	// labels only for the features it fired: one typed in by index that the
	// reading did not fire shows its index alone.
	function label(edit: DraftEdit): string | undefined {
		if (edit.vector_id) return steering.label(edit.vector_id);
		if (edit.site !== 'sae_feature' || edit.index == null || !inspector.reading) return undefined;
		return featureLabel(inspector.reading, edit.layer, edit.index);
	}

	// Each row is the reorder list's own button, so a click is read from
	// the row it landed on rather than from a handler inside it: anything
	// rendered in the row is inert by design, so that dragging works.
	function open(event: MouseEvent) {
		const row = (event.target as Element).closest<HTMLElement>('[data-reorder-id]');
		if (row?.dataset.reorderId) edits.active = row.dataset.reorderId;
	}

	function generate() {
		ongenerate();
		void generator.run({
			prompt: inspector.prompt,
			chat: inspector.chat,
			interventions: edits.interventions,
			maxNewTokens: length,
			target,
		});
	}
</script>

<section aria-labelledby="edits-heading" class="flex flex-col gap-3">
	<div class="flex items-center justify-between">
		<h2
			id="edits-heading"
			class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
		>
			Edits
		</h2>
		<Button variant="ghost" size="sm" class="h-7 gap-1.5 text-xs" onclick={onsteer}>
			<Compass size={13} aria-hidden="true" />
			Steer
		</Button>
	</div>

	{#if edits.items.length === 0}
		<p class="rounded-lg border border-dashed border-border p-3 text-xs leading-relaxed text-muted-foreground">
			Add a neuron or feature with <span class="font-mono">+</span> in the cell inspector, or steer
			with a vector. With no edits, both answers are the model's own.
		</p>
	{:else}
		<!-- The keyboard opens an edit from the list below this instead; see `open`. -->
		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
		<div onclick={open}>
			<ReorderList
				items={edits.items}
				getId={(edit) => edit.key}
				getLabel={(edit) => `${name(edit)}, ${editEffect(edit)}`}
				label="Edits, applied in this order where they share a site"
				onReorder={(items) => edits.reorder(items)}
				disabled={generator.running}
			>
				{#snippet children(edit)}
					<span
						class="flex items-center gap-2 text-xs {edit.key === edits.active
							? 'font-medium'
							: 'text-muted-foreground'}"
					>
						<span class="min-w-0 flex-1 truncate font-mono">{name(edit)}</span>
						<span class="shrink-0 font-mono tabular-nums">{editEffect(edit)}</span>
					</span>
				{/snippet}
			</ReorderList>
		</div>

		{#if edits.items.length > 1}
			<div class="flex items-center gap-2 text-[11px] text-muted-foreground">
				<span aria-hidden="true">Setting</span>
				<Select.Root
					value={edits.active ?? undefined}
					onValueChange={(key) => (edits.active = key)}
				>
					<!-- Named here rather than by Select.Value, which learns an item's
					     label only once the menu has opened, while the open edit is
					     usually chosen from outside it, by a row or by adding one. -->
					<Select.Trigger class="h-7 min-w-0 flex-1 font-mono text-[11px]" aria-label="Edit to set">
						<span class="truncate">{current ? name(current) : ''}</span>
					</Select.Trigger>
					<Select.Content>
						{#each edits.items as edit (edit.key)}
							<Select.Item value={edit.key} class="font-mono text-xs">{name(edit)}</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
			</div>
		{/if}

		{#if current}
			{@const key = current.key}
			<EditEditor
				edit={current}
				name={name(current)}
				onchange={(change) => edits.update(key, change)}
				onremove={() => edits.remove(key)}
			/>
		{/if}
	{/if}

	<div class="flex flex-col gap-3 rounded-lg border border-border bg-card p-3">
		<Slider
			bind:value={length}
			min={1}
			max={maxNewTokens}
			step={1}
			label="Answer length"
			format={(value) => `${value} tokens`}
			editable
		/>
		<label class="flex flex-col gap-1 text-[11px] text-muted-foreground">
			Watch a token
			<Input
				bind:value={target}
				placeholder=" Paris"
				class="h-8 font-mono text-xs"
				aria-describedby="target-hint"
			/>
			<span id="target-hint">
				Its logit at the first answer token, before and after. One token; a leading space counts.
			</span>
		</label>
		{#if generator.running}
			<Button variant="secondary" onclick={() => generator.stop()} class="gap-1.5">
				<Square size={13} aria-hidden="true" />
				Stop
			</Button>
		{:else}
			<Button onclick={generate} disabled={!runnable} class="gap-1.5">
				<Play size={13} aria-hidden="true" />
				Generate both
			</Button>
		{/if}
	</div>
</section>
