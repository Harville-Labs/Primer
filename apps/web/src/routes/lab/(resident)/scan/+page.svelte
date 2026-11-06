<script lang="ts">
	import LabFailure from '#lib/components/lab/LabFailure.svelte';
	import { Bookmark, ScanSearch, TriangleAlert } from '@lucide/svelte';
	import { Badge, Button, Progress, Select } from '@sivir-ui/svelte';
	import { onMount } from 'svelte';
	import ExperimentsSheet from '#lib/components/lab/ExperimentsSheet.svelte';
	import LabEmpty from '#lib/components/lab/LabEmpty.svelte';
	import LabShell from '#lib/components/lab/LabShell.svelte';
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

		<section aria-labelledby="library-heading" class="flex flex-col gap-2">
			<h2
				id="library-heading"
				class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
			>
				Library
			</h2>
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
		</section>

		<section aria-labelledby="probe-heading" class="flex flex-col gap-2">
			<h2
				id="probe-heading"
				class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
			>
				Rank by
			</h2>
			<Select.Root
				value={probeId ?? OFF}
				onValueChange={(value) => {
					probeId = value === OFF ? null : value;
				}}
			>
				<Select.Trigger class="h-9 w-full text-xs" disabled={scanner.running} aria-label="Probe to read">
					<span class="truncate">
						{probe ? `Probe: ${probe.label} · L${probe.layer}` : 'Mean surprisal'}
					</span>
				</Select.Trigger>
				<Select.Content>
					<Select.Item value={OFF} class="text-xs">Mean surprisal</Select.Item>
					{#each probes.items as each (each.id)}
						<Select.Item value={each.id} class="text-xs">
							Probe: {each.label} · L{each.layer}
						</Select.Item>
					{/each}
				</Select.Content>
			</Select.Root>
			<p class="text-[11px] leading-relaxed text-muted-foreground">
				{#if probes.items.length === 0}
					Train a probe on the Anomalies tab, on prompts that carry an instruction and prompts that
					do not, to rank passages by which they read like.
				{:else}
					A probe is read at each passage's last token, where it was trained to read.
				{/if}
			</p>
		</section>

		<div class="flex flex-col gap-2">
			{#if scanner.running}
				<Button variant="secondary" onclick={() => scanner.stop()}>Stop</Button>
			{:else}
				<Button disabled={!library || !data.status?.model} onclick={scan}>Scan library</Button>
			{/if}

			{#if scanner.failure}
				<LabFailure failure={scanner.failure} variant="warning" />
			{/if}
		</div>

		<p class="text-[11px] leading-relaxed text-muted-foreground">
			Each passage is read by {model ? modelName(model) : "Lab's model"} as plain text, a batch
			at a time between other people's runs. The scan runs in this tab: leaving the page stops it.
			A passage longer than Lab reads is scored from its start.
		</p>
	</div>
{/snippet}

{#snippet actions()}
	{#if retentionDays !== null && data.status?.model}
		<Button
			variant="ghost"
			size="icon"
			onclick={() => (savedOpen = true)}
			title="Save this scan, or open one saved before"
		>
			<Bookmark size={15} aria-hidden="true" />
			<span class="sr-only">Saved experiments</span>
		</Button>
	{/if}
{/snippet}

{#snippet scanStatus()}
	{#if scanner.library}
		<span class="tabular-nums">
			{scanner.library.name} · {scanner.scanned.toLocaleString()} of {scanner.listed.toLocaleString()}
			passages{#if scanner.phase === 'stopped'}&nbsp;· stopped{/if}
		</span>
		<span class="ml-auto">
			By {byProbe ? `the probe ${scanner.probe?.label}` : 'mean surprisal'}, most anomalous first
		</span>
	{/if}
{/snippet}

<!--
  For finding the passages in a library that a model reads as unlike the
  rest: the kind of text an injected instruction is. No right pane: a
  finding is read in place, and opened in its document from its link.
-->
<LabShell
	tool="scan"
	label="Findings"
	leftLabel="Library and ranking"
	left={settings}
	{actions}
	status={scanner.library ? scanStatus : undefined}
	bind:leftOpen={settingsOpen}
>
	{#if scanner.running}
		{#if scanner.phase === 'listing'}
			<Progress indeterminate class="h-[3px] shrink-0 rounded-none" />
		{:else}
			<Progress value={scanner.scanned} max={scanner.listed} class="h-[3px] shrink-0 rounded-none" />
		{/if}
	{/if}

	<div class="relative min-h-0 flex-1 overflow-auto overscroll-contain bg-card">
		{#if scanner.findings.length > 0 && scanner.library}
			<div class="flex flex-col gap-3 p-4">
				<p class="text-[11px] text-muted-foreground">
					The {Math.min(KEPT, scanner.findings.length)} most anomalous passages are kept.
					{#if scanner.total > scanner.listed}
						The library holds {scanner.total.toLocaleString()} passages; the first {scanner.listed.toLocaleString()}
						were scanned.
					{/if}
				</p>
				<ol class="divide-y divide-border rounded-lg border border-border">
					{#each scanner.findings as finding, index (finding.passage.chunk_id)}
						<li class="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 px-3 py-2.5">
							<span class="font-mono text-[11px] tabular-nums text-muted-foreground">
								{index + 1}
							</span>
							<div class="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
								<a
									href={passageLink(scanner.library.id, finding.passage)}
									class="truncate text-xs font-medium underline-offset-2 hover:underline"
								>
									{finding.passage.filename}{finding.passage.locator?.page
										? `, page ${finding.passage.locator.page}`
										: ''}
								</a>
								{#if finding.scored.truncated}
									<Badge variant="outline" title="Longer than Lab reads; scored from its start.">
										truncated
									</Badge>
								{/if}
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
							</div>
							<p
								class="col-start-2 line-clamp-2 whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground"
							>
								{finding.passage.content}
							</p>
						</li>
					{/each}
				</ol>
			</div>
		{:else if scanner.phase === 'done'}
			<LabEmpty icon={ScanSearch} title="Nothing was scored">
				The library has no indexed passages, or none long enough to score.
			</LabEmpty>
		{:else}
			<LabEmpty icon={ScanSearch} title="Find passages that do not belong" about="/lab/about/scan">
				Read every passage of a library through Lab's model and list the ones it found most
				surprising, or that a probe reads as instructions rather than text.
			</LabEmpty>
		{/if}
	</div>
</LabShell>

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
