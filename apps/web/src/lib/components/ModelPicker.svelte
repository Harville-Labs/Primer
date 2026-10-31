<script lang="ts">
	import { ChevronDown, Cpu, TriangleAlert } from '@lucide/svelte';
	import { untrack } from 'svelte';
	import { DropdownMenu } from '@sivir-ui/svelte';
	import type { ChatModel } from '#lib/api/types.ts';
	import { qualify } from '#lib/models.ts';

	interface Props {
		models: ChatModel[];
		/**
		 * The chosen model, as "<provider id>:<model id>".
		 *
		 * Qualified because model names are not unique across providers - two
		 * endpoints serving `llama3.1:8b` is ordinary - so the name alone
		 * cannot say which endpoint a question should go to.
		 */
		value?: string;
		/**
		 * Why there is no model, when there is none. Null when all is well.
		 *
		 * Carried as a sentence rather than a flag because the causes need
		 * different fixes: an inference endpoint that is down, one that is up
		 * with nothing loaded, and a Chat service that cannot be reached at
		 * all are three different jobs for whoever runs this.
		 */
		problem?: string | null;
	}

	let { models, value = $bindable(''), problem = null }: Props = $props();

	let chosen = $derived(models.find((model) => qualify(model) === value));
	let fallback = $derived(models.find((model) => model.default) ?? models[0]);
	let shown = $derived(chosen ?? fallback);

	// Say out loud which model is shown, rather than only displaying it.
	//
	// This used to display `fallback` while leaving `value` empty, so a
	// question carried no model unless someone opened the menu and picked a
	// different one - picking the one already highlighted changes nothing
	// and fires no event. An empty value means the request omits the model
	// entirely and the server falls back to whatever it was configured with,
	// which is how a deployment ends up answering from a model other than
	// the one named on screen. The name shown and the name sent have to be
	// the same name.
	//
	// Runs when `value` names nothing on offer, which covers both the empty
	// start and a stale choice whose provider has since been removed.
	$effect(() => {
		const settled = shown ? qualify(shown) : '';
		if (settled && chosen === undefined && untrack(() => value) !== settled) {
			value = settled;
		}
	});

	//: A menu whenever there is a model at all, even the only one.
	//:
	//: It used to flatten to plain text with one model, on the reasoning that
	//: a menu of one is a control that does nothing. That was true when a
	//: deployment had one endpoint. It is not now: the menu is where a
	//: reader sees which provider is answering and where more of them appear
	//: as they are added, so hiding it hides the feature from exactly the
	//: deployments that have not used it yet.
	let choosable = $derived(models.length > 0);

	//: Grouped so a reader can see where each model runs.
	//:
	//: The heading is drawn even when there is only one group. It used to be
	//: suppressed as noise, which pushed the provider name onto the trigger
	//: beside the model - two names of different kinds sitting side by side,
	//: reading as one long label rather than as a thing and where it runs.
	//: Inside the menu it has a column to head, which is what a provider
	//: name is: the answer to "where do these come from".
	let byProvider = $derived.by(() => {
		const groups = new Map<string, ChatModel[]>();
		for (const model of models) {
			const name = model.provider_name ?? 'This deployment';
			groups.set(name, [...(groups.get(name) ?? []), model]);
		}
		return [...groups];
	});
</script>

<!--
  Always says which model is answering, even when there is no choice to make.

  It used to hide itself entirely whenever a deployment served one model,
  which is the common case - so the usual experience was a chat window that
  never named the thing writing the answers. Which model wrote an answer is a
  fact about the answer, not a preference, and it belongs on screen whether
  or not it can be changed.
-->
{#if shown === undefined}
	<!--
	  No model, and the reason. Not a picker in an error colour: there is
	  nothing to pick, and a control implies otherwise. This used to show the
	  configured default name, which meant an endpoint that was down looked
	  exactly like one that was up.
	-->
	<span
		class="flex items-center gap-2 px-1 py-1.5 text-sm text-error"
		title={problem ?? 'No model is available.'}
	>
		<TriangleAlert size={14} aria-hidden="true" />
		<span class="max-w-[22rem] truncate">No model available</span>
	</span>
{:else if choosable}
	<DropdownMenu.Root>
		<DropdownMenu.Trigger variant="ghost" size="sm" class="text-muted-foreground">
			<Cpu size={14} aria-hidden="true" />
			<span
				class="max-w-[16rem] truncate font-mono text-xs"
				title="{shown.id}{shown.provider_name ? ` on ${shown.provider_name}` : ''}"
			>
				{shown.id}
			</span>
			<!--
			  The affordance. Without it this is a model name that happens to
			  be clickable, and nobody clicks a label: the menu was reported
			  missing by someone looking straight at it. The chevron is the
			  convention for "this opens", and it costs one glyph.
			-->
			<ChevronDown size={13} aria-hidden="true" class="shrink-0 opacity-60" />
		</DropdownMenu.Trigger>
		<DropdownMenu.Content>
			<DropdownMenu.RadioGroup
				value={value || qualify(shown)}
				onValueChange={(next) => (value = next)}
			>
				{#each byProvider as [provider, offered], index (provider)}
					<!--
					  Which machine each model runs on is the thing being
					  chosen between as much as the model is: the local one is
					  free and private, the hosted one is neither. Always
					  drawn, including for a lone provider - it is the column
					  heading for the models under it, and a list whose
					  heading appears only once a second one exists teaches
					  nobody where the first came from.
					-->
					<p
						class="px-2.5 pb-1 text-[10px] font-medium uppercase tracking-[0.09em]
							text-muted-foreground {index > 0 ? 'mt-1 border-t border-border pt-2' : 'pt-1'}"
					>
						{provider}
					</p>
					{#each offered as model (qualify(model))}
						<DropdownMenu.RadioItem value={qualify(model)}>
							<span class="flex w-full items-center justify-between gap-4">
								<span class="truncate">{model.id}</span>
								{#if model.default}
									<span class="text-xs text-muted-foreground">Default</span>
								{/if}
							</span>
						</DropdownMenu.RadioItem>
					{/each}
				{/each}
			</DropdownMenu.RadioGroup>
		</DropdownMenu.Content>
	</DropdownMenu.Root>
{/if}
