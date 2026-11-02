<script lang="ts" generics="K extends ExperimentKind">
	import { Check, Pencil, Trash2, X } from '@lucide/svelte';
	import { Button, Input, Sheet, Spinner } from '@sivir-ui/svelte';
	import type { Experiment, ExperimentKind } from '#lib/api/types.ts';
	import type { Experiments, RunOf } from '#lib/lab/experiments.svelte.ts';
	import { exactly, timeAgo } from '#lib/when.ts';

	/**
	 * Saving the run on screen, and opening, renaming and deleting the ones
	 * saved before. Shared by Neurons, Anomalies and Scan, which differ only in
	 * what a run is and how it is shown.
	 */
	interface Props {
		open: boolean;
		experiments: Experiments<K>;
		/** The operator's retention period, which the user is told before saving. */
		retentionDays: number;
		/** The run a save would store, or null when there is nothing to save yet. */
		run: RunOf<K> | null;
		/** Why there is nothing to save, said where the form would be. */
		unsaved: string;
		/** The title the form starts with: the prompt, usually. */
		suggested: string;
		onopen: (experiment: Experiment) => void;
	}

	let {
		open = $bindable(),
		experiments,
		retentionDays,
		run,
		unsaved,
		suggested,
		onopen,
	}: Props = $props();

	// Derived so a new run suggests its own title, and still free to be typed over.
	let title = $derived(suggested.trim().slice(0, 80));
	let renaming = $state<string | null>(null);
	let renamed = $state('');
	let deleting = $state<string | null>(null);

	async function save() {
		if (run && title.trim()) await experiments.save(title.trim(), run);
	}

	async function rename(id: string) {
		if (renamed.trim() && (await experiments.rename(id, renamed.trim()))) renaming = null;
	}

	async function remove(id: string) {
		if (await experiments.remove(id)) deleting = null;
	}

	async function reopen(id: string) {
		const experiment = await experiments.open(id);
		if (experiment) {
			onopen(experiment);
			open = false;
		}
	}

	function until(iso: string): string {
		const when = new Date(iso);
		return Number.isNaN(when.getTime()) ? '' : when.toLocaleDateString(undefined, { dateStyle: 'medium' });
	}

	const heading = 'font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground';
</script>

<Sheet.Root bind:open>
	<Sheet.Content side="right" class="flex w-[26rem] max-w-full flex-col gap-6 overflow-y-auto p-5">
		<Sheet.Header>
			<Sheet.Title>Saved experiments</Sheet.Title>
			<Sheet.Description>
				Nothing is kept unless you save it. A saved experiment keeps its request, its result, and
				the vectors and probes it used, for you alone, for {retentionDays}
				{retentionDays === 1 ? 'day' : 'days'}.
			</Sheet.Description>
		</Sheet.Header>

		<section aria-labelledby="save-heading" class="flex flex-col gap-2">
			<h3 id="save-heading" class={heading}>Save this run</h3>
			{#if run}
				<form
					class="flex gap-2"
					onsubmit={(event) => {
						event.preventDefault();
						void save();
					}}
				>
					<Input bind:value={title} aria-label="Title" maxlength={200} class="h-8 text-sm" />
					<Button type="submit" disabled={!title.trim() || experiments.busy} class="gap-1.5">
						{#if experiments.busy}<Spinner size={13} aria-hidden="true" />{/if}
						Save
					</Button>
				</form>
			{:else}
				<p class="text-xs leading-relaxed text-muted-foreground">{unsaved}</p>
			{/if}
		</section>

		{#if experiments.failure}
			<p role="alert" class="text-xs">
				<span class="font-medium text-error">{experiments.failure.title}.</span>
				{experiments.failure.detail}
			</p>
		{/if}

		<section aria-labelledby="saved-heading" class="flex flex-col gap-2">
			<h3 id="saved-heading" class={heading}>Saved</h3>
			{#if experiments.items.length === 0}
				<p class="text-xs text-muted-foreground">None yet.</p>
			{:else}
				<ul class="flex flex-col gap-1">
					{#each experiments.items as experiment (experiment.id)}
						<li class="flex items-start gap-1 rounded-md border border-border px-3 py-2">
							{#if renaming === experiment.id}
								<form
									class="flex flex-1 gap-1"
									onsubmit={(event) => {
										event.preventDefault();
										void rename(experiment.id);
									}}
								>
									<!-- Focused: the pencil that opened it has gone, and focus with it. -->
									<!-- svelte-ignore a11y_autofocus -->
									<Input
										bind:value={renamed}
										autofocus
										aria-label="New title"
										maxlength={200}
										class="h-7 text-sm"
									/>
									<Button type="submit" variant="ghost" size="icon" disabled={!renamed.trim()}>
										<Check size={14} aria-hidden="true" />
										<span class="sr-only">Rename</span>
									</Button>
									<Button variant="ghost" size="icon" onclick={() => (renaming = null)}>
										<X size={14} aria-hidden="true" />
										<span class="sr-only">Keep the title</span>
									</Button>
								</form>
							{:else}
								<button
									type="button"
									onclick={() => reopen(experiment.id)}
									disabled={experiments.busy}
									class="flex min-w-0 flex-1 flex-col gap-0.5 text-left focus-visible:outline-none
										focus-visible:shadow-[var(--focus-ring)]"
								>
									<span class="truncate text-sm font-medium">{experiment.title}</span>
									<span class="text-[11px] text-muted-foreground">
										<span title={exactly(experiment.created_at)}>
											Saved {timeAgo(experiment.created_at)}
										</span>
										·
										<span title={exactly(experiment.expires_at)}>
											kept until {until(experiment.expires_at)}
										</span>
									</span>
								</button>
								{#if deleting === experiment.id}
									<Button
										variant="destructive"
										size="sm"
										disabled={experiments.busy}
										onclick={() => remove(experiment.id)}
									>
										Delete
									</Button>
									<Button variant="ghost" size="icon" onclick={() => (deleting = null)}>
										<X size={14} aria-hidden="true" />
										<span class="sr-only">Keep it</span>
									</Button>
								{:else}
									<Button
										variant="ghost"
										size="icon"
										onclick={() => {
											renamed = experiment.title;
											renaming = experiment.id;
										}}
									>
										<Pencil size={14} aria-hidden="true" />
										<span class="sr-only">Rename {experiment.title}</span>
									</Button>
									<Button variant="ghost" size="icon" onclick={() => (deleting = experiment.id)}>
										<Trash2 size={14} aria-hidden="true" />
										<span class="sr-only">Delete {experiment.title}</span>
									</Button>
								{/if}
							{/if}
						</li>
					{/each}
				</ul>
				<p class="text-[11px] text-muted-foreground">
					Opening one puts its vectors and probes back, on the model it was saved on.
				</p>
			{/if}
		</section>
	</Sheet.Content>
</Sheet.Root>
