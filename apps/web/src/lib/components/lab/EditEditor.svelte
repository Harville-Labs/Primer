<script lang="ts">
	import { Trash2 } from '@lucide/svelte';
	import { Button, Slider, ToggleGroup } from '@sivir-ui/svelte';
	import type { Intervention, InterventionOp } from '#lib/api/types.ts';
	import type { DraftEdit } from '#lib/lab/edits.svelte.ts';
	import { OP_RANGES, OPS } from '#lib/lab/generation.ts';

	/** The controls for one edit: what it does, how hard, and where. */
	interface Props {
		edit: DraftEdit;
		name: string;
		onchange: (change: Partial<Intervention>) => void;
		onremove: () => void;
	}

	let { edit, name, onchange, onremove }: Props = $props();

	const POSITIONS = [
		{ value: 'all', label: 'All', hint: 'Every text token, prompt and answer' },
		{ value: 'prompt', label: 'Prompt', hint: 'Only the prompt’s text tokens' },
		{ value: 'generated', label: 'Answer', hint: 'Only the tokens the model writes' },
	] as const;

	// A steering vector is only ever added: scaling or clamping the stream
	// "by a vector" is not an operation Lab has.
	let steering = $derived(Boolean(edit.vector_id));
	let range = $derived(OP_RANGES[edit.op]);
	const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

	const HINTS: Record<InterventionOp, string> = {
		zero: 'Set it to zero, as if it never fired.',
		mean: 'Set it to its average over the prompt, removing what this token adds.',
		scale: 'Multiply it. Below 1 damps it, negative flips it.',
		clamp: 'Hold it at one value at every position.',
		add: 'Add to it at every position.',
	};
</script>

<div class="flex flex-col gap-3 rounded-lg border border-border bg-card p-3">
	<div class="flex items-center gap-2">
		<p class="min-w-0 flex-1 truncate font-mono text-xs font-medium" title={name}>{name}</p>
		<Button variant="ghost" size="icon" class="size-7" onclick={onremove} title="Remove this edit">
			<Trash2 size={13} aria-hidden="true" />
			<span class="sr-only">Remove {name}</span>
		</Button>
	</div>

	{#if !steering}
		<div class="flex flex-col gap-1.5">
			<ToggleGroup.Root
				type="single"
				value={edit.op}
				onValueChange={(value) => {
					if (typeof value === 'string' && value) onchange({ op: value as InterventionOp });
				}}
				class="w-full"
			>
				{#each OPS as op (op.value)}
					<ToggleGroup.Item value={op.value} class="flex-1 px-1 text-[11px]">
						{op.label}
					</ToggleGroup.Item>
				{/each}
			</ToggleGroup.Root>
			<p class="text-[11px] leading-snug text-muted-foreground">{HINTS[edit.op]}</p>
		</div>
	{/if}

	{#if range}
		<Slider
			value={edit.value}
			min={range.min}
			max={range.max}
			step={range.step}
			label={steering ? 'Strength' : edit.op === 'scale' ? 'Factor' : 'Value'}
			format={(value) => (edit.op === 'scale' ? `×${number.format(value)}` : number.format(value))}
			editable
			onValueChange={(value) => onchange({ value })}
		/>
	{/if}

	<div class="flex flex-col gap-1.5">
		<span class="text-[11px] text-muted-foreground">Positions</span>
		<ToggleGroup.Root
			type="single"
			value={edit.positions as string}
			onValueChange={(value) => {
				if (value === 'all' || value === 'prompt' || value === 'generated') {
					onchange({ positions: value });
				}
			}}
			class="w-full"
		>
			{#each POSITIONS as each (each.value)}
				<ToggleGroup.Item value={each.value} class="flex-1 text-[11px]" title={each.hint}>
					{each.label}
				</ToggleGroup.Item>
			{/each}
		</ToggleGroup.Root>
	</div>
</div>
