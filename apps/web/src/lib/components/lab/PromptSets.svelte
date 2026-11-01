<script lang="ts">
	import { Input, Slider, Textarea } from '@sivir-ui/svelte';
	import { promptLines } from '#lib/lab/prompts.ts';

	/**
	 * A label, a layer, and two sets of prompts: what a steering vector and a
	 * probe are both made from, read at each prompt's last token.
	 */
	interface Props {
		label: string;
		layer: number;
		positive: string;
		negative: string;
		numLayers: number;
		//: What is being named, for a screen reader: "Vector label".
		labelName: string;
		labelPlaceholder: string;
		//: What each set is called beside its box: "Towards", "Away from".
		names: [string, string];
		placeholders: [string, string];
	}

	let {
		label = $bindable(),
		layer = $bindable(),
		positive = $bindable(),
		negative = $bindable(),
		numLayers,
		labelName,
		labelPlaceholder,
		names,
		placeholders,
	}: Props = $props();
</script>

<Input bind:value={label} placeholder={labelPlaceholder} aria-label={labelName} maxlength={80} />
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
	label={names[0]}
	description="One prompt a line. {promptLines(positive).length} so far."
	placeholder={placeholders[0]}
	rows={3}
	autoresize
/>
<Textarea
	bind:value={negative}
	label={names[1]}
	description="One prompt a line. {promptLines(negative).length} so far."
	placeholder={placeholders[1]}
	rows={3}
	autoresize
/>
