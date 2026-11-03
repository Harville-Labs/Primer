<script lang="ts">
	import { Cpu, TriangleAlert } from '@lucide/svelte';
	import { Composer, Spinner, Switch } from '@sivir-ui/svelte';
	import type { Snippet } from 'svelte';
	import type { LabModelStatus, LabStatus } from '#lib/api/types.ts';
	import type { Inspector } from '#lib/lab/inspector.svelte.ts';
	import { modelName } from '#lib/lab/model.ts';

	/** The prompt, the model it runs on, and what happened when it last ran. */
	interface Props {
		inspector: Inspector;
		//: Null when Lab did not answer for the page; `problem` then says why.
		status: LabStatus | null;
		problem: { title: string; detail: string } | null;
		//: What else runs on this prompt, under it and above the limits.
		children?: Snippet;
	}

	let { inspector, status, problem, children }: Props = $props();

	// The reading's own model over the status's: the status was read when the
	// page loaded, and the grid beside this belongs to whatever ran it.
	let model = $derived<LabModelStatus | null>(inspector.reading?.model ?? status?.model ?? null);
	let name = $derived(model ? modelName(model) : null);
	let pinned = $derived(
		model?.reference.source === 'hub' ? (model.commit ?? model.reference.revision) : null
	);
	let runnable = $derived(Boolean(model) && !inspector.running);
</script>

<div class="flex h-full min-h-0 flex-col gap-5 px-4 py-4">
	<section aria-labelledby="model-heading" class="flex flex-col gap-2">
		<h2
			id="model-heading"
			class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
		>
			Resident model
		</h2>
		{#if model && name}
			<div class="flex flex-col gap-2 rounded-lg border border-border bg-card p-3">
				<p class="break-all font-mono text-sm font-medium leading-snug" title={name}>{name}</p>
				{#if pinned}
					<p class="truncate font-mono text-[11px] text-muted-foreground" title={pinned}>
						@ {pinned.slice(0, 12)}
					</p>
				{/if}
				<dl class="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
					<dt class="text-muted-foreground">Architecture</dt>
					<dd class="truncate text-right font-mono" title={model.architecture}>
						{model.architecture}
					</dd>
					<dt class="text-muted-foreground">Layers</dt>
					<dd class="text-right font-mono tabular-nums">{model.num_layers}</dd>
					<dt class="text-muted-foreground">Running on</dt>
					<dd class="flex items-center justify-end gap-1 font-mono">
						<Cpu size={11} aria-hidden="true" />{model.device} · {model.dtype}
					</dd>
				</dl>
			</div>
		{:else if problem}
			<div role="alert" class="flex gap-2 rounded-lg border border-border bg-card p-3 text-xs">
				<TriangleAlert size={14} aria-hidden="true" class="mt-px shrink-0 text-warning" />
				<p><span class="font-medium">{problem.title}.</span> {problem.detail}</p>
			</div>
		{:else}
			<p class="rounded-lg border border-dashed border-border p-3 text-xs text-muted-foreground">
				No model is loaded. An operator sets Lab's model with
				<code class="font-mono">PRIMER_LAB_MODEL</code>.
			</p>
		{/if}
	</section>

	<section aria-labelledby="prompt-heading" class="flex flex-col gap-3">
		<h2
			id="prompt-heading"
			class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
		>
			Prompt
		</h2>
		<Composer.Root
			bind:value={inspector.prompt}
			disabled={!runnable}
			onSubmit={() => inspector.run()}
		>
			<Composer.Input
				placeholder={model ? 'The capital of France is' : 'No model to run it on.'}
				aria-label="Prompt to inspect"
			/>
			<Composer.Toolbar>
				<Composer.Actions></Composer.Actions>
				<Composer.Submit />
			</Composer.Toolbar>
		</Composer.Root>
		<Switch
			bind:checked={inspector.chat}
			disabled={inspector.running}
			label="Chat template"
			description="Wrap the prompt the way the model sees a chat message. Off reads it as raw text."
		/>
	</section>

	{#if inspector.running}
		<p role="status" class="flex items-center gap-2 text-xs text-muted-foreground">
			<Spinner size={12} aria-hidden="true" />
			{#if inspector.ahead}
				Waiting behind {inspector.ahead}
				{inspector.ahead === 1 ? 'run' : 'runs'}…
			{:else}
				Reading every layer…
			{/if}
		</p>
	{/if}

	{#if inspector.failure}
		<div role="alert" class="rounded-lg border border-error/40 bg-error/5 p-3 text-xs">
			<p class="font-medium text-error">{inspector.failure.title}</p>
			<p class="mt-1 text-foreground">{inspector.failure.detail}</p>
			{#if inspector.reading}
				<p class="mt-1 text-muted-foreground">The grid still shows the last reading.</p>
			{/if}
		</div>
	{/if}

	{@render children?.()}

	{#if status}
		<p class="mt-auto text-[11px] leading-relaxed text-muted-foreground">
			Up to {status.max_prompt_tokens.toLocaleString()} tokens a prompt. Runs are queued one at a
			time across everyone using this Lab.
		</p>
	{/if}
</div>
