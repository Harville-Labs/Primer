<script lang="ts">
	import { CircleHelp, PanelLeft, PanelRight } from '@lucide/svelte';
	import { Button, Sheet } from '@sivir-ui/svelte';
	import type { Snippet } from 'svelte';
	import { LAB_TOOLS, type LabTool } from '#lib/lab/tools.ts';

	interface Props {
		tool: LabTool;
		/** What the middle pane holds, for its landmark. */
		label: string;
		/** What the left pane is, for the button and sheet that stand in for it on a narrow screen. */
		leftLabel: string;
		left: Snippet;
		rightLabel?: string;
		right?: Snippet;
		/** Beside the title: which view of the tool is showing. */
		views?: Snippet;
		/** Before the explainer link, at the end of the header. */
		actions?: Snippet;
		/**
		 * A second row: what the current reading is, and how it is drawn. Its
		 * own row so the header never wraps, which in a pane this narrow it
		 * otherwise does at every desktop width.
		 */
		status?: Snippet;
		leftOpen?: boolean;
		children: Snippet;
	}

	let {
		tool,
		label,
		leftLabel,
		left,
		rightLabel,
		right,
		views,
		actions,
		status,
		leftOpen = $bindable(false),
		children,
	}: Props = $props();

	let rightOpen = $state(false);
	let entry = $derived(LAB_TOOLS.find((each) => each.tool === tool) ?? LAB_TOOLS[0]);
</script>

<!--
  Every Lab tool is laid out the same way - what to read on the left, the
  reading in the middle, the selected part of it on the right - so moving
  between them is not relearning where things are. On a desktop all three
  panes are in view, because the middle one is only worth reading beside
  the other two. Below the breakpoint the middle keeps the screen and the
  others come in from the edges, rather than stacking into a column where
  the reading is a thumbnail.
-->
<div
	class="flex h-full min-h-0 overflow-hidden rounded-xl border border-border bg-card
		shadow-[var(--elevation-float)]"
>
	<aside class="hidden w-72 shrink-0 overflow-y-auto border-r border-border bg-muted/30 lg:block">
		{@render left()}
	</aside>

	<section aria-label={label} class="flex min-w-0 flex-1 flex-col">
		<div class="flex h-12 shrink-0 items-center gap-3 border-b border-border px-4">
			<Button variant="ghost" size="icon" class="lg:hidden" onclick={() => (leftOpen = true)}>
				<PanelLeft size={15} aria-hidden="true" />
				<span class="sr-only">{leftLabel}</span>
			</Button>

			<h1 class="text-sm font-semibold tracking-[-0.01em]">{entry.label}</h1>
			{@render views?.()}

			<div class="ml-auto flex items-center gap-1">
				{@render actions?.()}
				<Button variant="ghost" size="sm" href={entry.about} class="gap-1.5 text-xs text-muted-foreground">
					<CircleHelp size={14} aria-hidden="true" />
					<span class="hidden sm:inline">What is this?</span>
					<span class="sr-only sm:hidden">What is {entry.label}?</span>
				</Button>
				{#if right}
					<Button variant="ghost" size="icon" class="lg:hidden" onclick={() => (rightOpen = true)}>
						<PanelRight size={15} aria-hidden="true" />
						<span class="sr-only">{rightLabel}</span>
					</Button>
				{/if}
			</div>
		</div>

		{#if status}
			<div
				class="flex min-h-9 shrink-0 flex-wrap items-center gap-x-4 gap-y-1.5 border-b border-border
					bg-muted/20 px-4 py-1.5 font-mono text-[11px] text-muted-foreground"
			>
				{@render status()}
			</div>
		{/if}

		{@render children()}
	</section>

	{#if right}
		<aside class="hidden w-80 shrink-0 overflow-y-auto border-l border-border bg-muted/30 lg:block">
			{@render right()}
		</aside>
	{/if}
</div>

<Sheet.Root bind:open={leftOpen}>
	<Sheet.Content side="left" class="w-80 overflow-y-auto p-0">
		{@render left()}
	</Sheet.Content>
</Sheet.Root>

{#if right}
	<Sheet.Root bind:open={rightOpen}>
		<Sheet.Content side="right" class="w-80 overflow-y-auto p-0">
			{@render right()}
		</Sheet.Content>
	</Sheet.Root>
{/if}
