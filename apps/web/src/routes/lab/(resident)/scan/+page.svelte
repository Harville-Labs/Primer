<script lang="ts">
	import { Bookmark, PanelLeft, ScanSearch, TriangleAlert } from '@lucide/svelte';
	import { Badge, Button, Progress, Select, Sheet } from '@sivir-ui/svelte';
	import { onMount } from 'svelte';
	import ExperimentsSheet from '#lib/components/lab/ExperimentsSheet.svelte';
	import type { Experiment, ScanRun } from '#lib/api/types.ts';
	import { Experiments } from '#lib/lab/experiments.svelte.ts';
	import { modelName } from '#lib/lab/model.ts';
	import { KEPT, passageLink } from '#lib/lab/scan.ts';
	import { Scanner } from '#lib/lab/scanner.svelte.ts';
	import { Probes } from '#lib/lab/scorer.svelte.ts';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const scanner = new Scanner();
	const probes = new Probes();
	const experiments = new Experiments('scan');

	const OFF = 'off';
	let libraryId = $state<string | null>(null);
	let probeId = $state<string | null>(null);
	let settingsOpen = $state(false);
	let savedOpen = $state(false);

	let library = $derived(data.libraries.find((each) => each.id === libraryId) ?? null);
	let probe = $derived(probes.items.find((each) => each.id === probeId) ?? null);
	let model = $derived(scanner.model ?? data.status?.model ?? null);
	// Null when Lab has no database, and saving is not offered at all.
	let retentionDays = $derived(data.status?.experiment_retention_days ?? null);
	let saveable = $derived<ScanRun | null>(scanner.phase === 'done' ? scanner.run() : null);
	let byProbe = $derived(scanner.probe !== null);

	onMount(() => {
		if (data.status?.model) void probes.load();
		if (data.status?.model && retentionDays !== null) void experiments.load();
	});

	function scan() {
		if (library) void scanner.start({ id: library.id, name: library.name }, probeId);
		settingsOpen = false;
	}

	function reopen(experiment: Experiment) {
		if (experiment.run.kind !== 'scan') return;
		scanner.show(experiment.run);
		libraryId = experiment.run.request.library_id;
		probeId = experiment.run.request.probe_id ?? null;
		// Read again: the restore put the scan's probe back under its id.
		void probes.load();
	}

	function bits(value: number | null | undefined): string {
		return value === null || value === undefined ? '-' : value.toFixed(2);
	}
</script>

<svelte:head>
	<title>Scan · Lab · Primer</title>
</svelte:head>

{#snippet settings()}
	<div class="flex flex-col gap-6 p-4">
		<section aria-labelledby="scan-heading" class="flex flex-col gap-4">
			<h2
				id="scan-heading"
				class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
			>
				Scan
			</h2>

			{#if !data.status?.model}
				<div role="alert" class="flex gap-2 rounded-lg border border-border bg-card p-3 text-xs">
					<TriangleAlert size={14} aria-hidden="true" class="mt-px shrink-0 text-warning" />
					<p>
						{#if data.statusProblem}
							<span class="font-medium">{data.statusProblem.title}.</span>
							{data.statusProblem.detail}
						{:else}
							No model is loaded. An operator sets Lab's model with
							<code class="font-mono">PRIMER_LAB_MODEL</code>.
						{/if}
					</p>
				</div>
			{/if}

			<div class="flex flex-col gap-1.5">
				<span aria-hidden="true" class="text-xs font-medium">Library</span>
				<Select.Root
					value={libraryId ?? undefined}
					onValueChange={(value) => {
						libraryId = value;
					}}
				>
					<Select.Trigger
						class="h-9 w-full text-xs"
						disabled={scanner.running}
						aria-label="Library to scan"
					>
						<!-- As elsewhere in Lab: Select.Value is blank until the menu opens. -->
						<span class="truncate">{library?.name ?? 'Choose a library'}</span>
					</Select.Trigger>
					<Select.Content>
						{#each data.libraries as each (each.id)}
							<Select.Item value={each.id} class="text-xs">{each.name}</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
				{#if data.libraries.length === 0}
					<p class="text-[11px] text-muted-foreground">You have no libraries to scan yet.</p>
				{/if}
			</div>

			<div class="flex flex-col gap-1.5">
				<span aria-hidden="true" class="text-xs font-medium">Probe</span>
				<Select.Root
					value={probeId ?? OFF}
					onValueChange={(value) => {
						probeId = value === OFF ? null : value;
					}}
				>
					<Select.Trigger
						class="h-9 w-full text-xs"
						disabled={scanner.running}
						aria-label="Probe to read"
					>
						<span class="truncate">{probe ? `${probe.label} · L${probe.layer}` : 'Off'}</span>
					</Select.Trigger>
					<Select.Content>
						<Select.Item value={OFF} class="text-xs">Off: rank by surprisal</Select.Item>
						{#each probes.items as each (each.id)}
							<Select.Item value={each.id} class="text-xs">{each.label} · L{each.layer}</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
				<p class="text-[11px] leading-relaxed text-muted-foreground">
					{#if probes.items.length === 0}
						Train a probe on the Anomalies tab, on prompts that carry an instruction and prompts
						that do not, to rank passages by which they read like.
					{:else}
						Read at each passage's last token, where a probe was trained to read.
					{/if}
				</p>
			</div>

			{#if scanner.running}
				<Button variant="secondary" onclick={() => scanner.stop()}>Stop</Button>
			{:else}
				<Button disabled={!library || !data.status?.model} onclick={scan}>Scan library</Button>
			{/if}

			{#if scanner.failure}
				<div role="alert" class="flex gap-2 rounded-lg border border-border bg-card p-3 text-xs">
					<TriangleAlert size={14} aria-hidden="true" class="mt-px shrink-0 text-warning" />
					<p><span class="font-medium">{scanner.failure.title}.</span> {scanner.failure.detail}</p>
				</div>
			{/if}
		</section>

		<p class="text-[11px] leading-relaxed text-muted-foreground">
			Each passage is read by {model ? modelName(model) : "Lab's model"} as plain text, a batch
			at a time between other people's runs. The scan runs in this tab: leaving the page stops it.
			A passage longer than Lab reads is scored from its start.
		</p>
	</div>
{/snippet}

<!--
  For finding the passages in a library that a model reads as unlike the
  rest: the kind of text an injected instruction is. Laid out like
  Anomalies, with the settings beside a ranked list.
-->
<div
	class="flex h-full min-h-0 overflow-hidden rounded-xl border border-border bg-card
		shadow-[var(--elevation-float)]"
>
	<aside class="hidden w-72 shrink-0 overflow-y-auto border-r border-border bg-muted/30 lg:block">
		{@render settings()}
	</aside>

	<section aria-label="Findings" class="flex min-w-0 flex-1 flex-col">
		<div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-2.5">
			<Button variant="ghost" size="icon" class="lg:hidden" onclick={() => (settingsOpen = true)}>
				<PanelLeft size={15} aria-hidden="true" />
				<span class="sr-only">Library and probe</span>
			</Button>

			<div class="flex items-baseline gap-2">
				<h1 class="text-sm font-semibold tracking-[-0.01em]">Scan</h1>
				{#if scanner.library}
					<span class="font-mono text-[11px] tabular-nums text-muted-foreground">
						{scanner.library.name} · {scanner.scanned.toLocaleString()} of {scanner.listed.toLocaleString()}
						passages
						{#if scanner.phase === 'stopped'}· stopped{/if}
					</span>
				{/if}
			</div>

			{#if retentionDays !== null && data.status?.model}
				<Button
					variant="ghost"
					size="icon"
					class="ml-auto"
					onclick={() => (savedOpen = true)}
					title="Save this scan, or open one saved before"
				>
					<Bookmark size={15} aria-hidden="true" />
					<span class="sr-only">Saved experiments</span>
				</Button>
			{/if}
		</div>

		{#if scanner.running}
			{#if scanner.phase === 'listing'}
				<Progress indeterminate class="h-[3px] rounded-none" />
			{:else}
				<Progress value={scanner.scanned} max={scanner.listed} class="h-[3px] rounded-none" />
			{/if}
		{/if}

		<div class="relative min-h-0 flex-1 overflow-auto overscroll-contain bg-card">
			{#if scanner.findings.length > 0 && scanner.library}
				<div class="flex flex-col gap-3 p-4">
					<p class="text-[11px] text-muted-foreground">
						The {Math.min(KEPT, scanner.findings.length)} most anomalous, by
						{byProbe ? `the probe ${scanner.probe?.label}` : 'mean surprisal'}, most anomalous first.
						{#if scanner.total > scanner.listed}
							The library holds {scanner.total.toLocaleString()} passages; the first {scanner.listed.toLocaleString()}
							were scanned.
						{/if}
					</p>
					<ol class="flex flex-col gap-2">
						{#each scanner.findings as finding, index (finding.passage.chunk_id)}
							<li class="flex gap-3 rounded-lg border border-border p-3">
								<span class="w-6 shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
									{index + 1}
								</span>
								<div class="flex min-w-0 flex-1 flex-col gap-1.5">
									<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
										<a
											href={passageLink(scanner.library.id, finding.passage)}
											class="truncate text-xs font-medium underline-offset-2 hover:underline"
										>
											{finding.passage.filename}{finding.passage.locator?.page
												? `, page ${finding.passage.locator.page}`
												: ''}
										</a>
										<dl class="ml-auto flex items-baseline gap-2 font-mono text-[11px] text-muted-foreground">
											{#if byProbe}
												<dt>Probe</dt>
												<dd class="tabular-nums text-foreground">
													{finding.scored.probe === null || finding.scored.probe === undefined
														? '-'
														: finding.scored.probe.toFixed(2)}
												</dd>
											{/if}
											<dt>Mean</dt>
											<dd class="tabular-nums text-foreground">{bits(finding.scored.mean_bits)}</dd>
											<dt>Peak</dt>
											<dd class="tabular-nums text-foreground">{bits(finding.scored.peak_bits)} bits</dd>
										</dl>
										{#if finding.scored.truncated}
											<Badge variant="outline" title="Longer than Lab reads; scored from its start.">
												truncated
											</Badge>
										{/if}
									</div>
									<p class="line-clamp-3 whitespace-pre-wrap text-sm leading-relaxed">
										{finding.passage.content}
									</p>
								</div>
							</li>
						{/each}
					</ol>
				</div>
			{:else}
				<div class="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
					<span
						aria-hidden="true"
						class="grid size-12 place-items-center rounded-xl border border-border"
						style:background-image="linear-gradient(135deg, var(--color-card), color-mix(in oklab, var(--color-lab-positive) 35%, var(--color-card)))"
					>
						<ScanSearch size={18} class="text-[var(--color-lab-positive)]" />
					</span>
					<p class="text-sm font-medium">
						{scanner.phase === 'done' ? 'Nothing was scored' : 'Find passages that do not belong'}
					</p>
					<p class="max-w-sm text-xs leading-relaxed text-muted-foreground">
						{#if scanner.phase === 'done'}
							The library has no indexed passages, or none long enough to score.
						{:else}
							Read every passage of a library through Lab's model and list the ones it found most
							surprising, or that a probe reads as instructions rather than text.
						{/if}
					</p>
				</div>
			{/if}
		</div>
	</section>
</div>

<Sheet.Root bind:open={settingsOpen}>
	<Sheet.Content side="left" class="w-80 overflow-y-auto p-0">
		{@render settings()}
	</Sheet.Content>
</Sheet.Root>

{#if retentionDays !== null}
	<ExperimentsSheet
		bind:open={savedOpen}
		{experiments}
		{retentionDays}
		run={saveable}
		unsaved="Scan a library first. A scan is saved with its fifty most anomalous passages, so their text is kept with it."
		suggested={scanner.library ? `Scan of ${scanner.library.name}` : ''}
		onopen={reopen}
	/>
{/if}
