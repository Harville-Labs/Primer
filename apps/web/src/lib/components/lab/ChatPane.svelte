<script lang="ts">
	import LabFailure from '#lib/components/lab/LabFailure.svelte';
	import { MessagesSquare, Send, Square } from '@lucide/svelte';
	import { Button, Slider, Spinner, Textarea } from '@sivir-ui/svelte';
	import type { Intervention } from '#lib/api/types.ts';
	import type { Exchange, SteeredChat } from '#lib/lab/chat.svelte.ts';
	import type { Edits, Steering } from '#lib/lab/edits.svelte.ts';
	import { editEffect, editTarget, markTokens } from '#lib/lab/generation.ts';
	import MarkedAnswer from './MarkedAnswer.svelte';

	/**
	 * A conversation with the resident model while the edits list steers it.
	 *
	 * The edits are the ones in the list beside this pane, read when each
	 * turn is sent, so changing or clearing them between turns steers the
	 * next reply differently and leaves the replies already written alone.
	 */
	interface Props {
		chat: SteeredChat;
		edits: Edits;
		steering: Steering;
		//: The deployment's limit on a reply's length.
		maxNewTokens: number;
	}

	let { chat, edits, steering, maxNewTokens }: Props = $props();

	let said = $state('');
	// Follows the limit until it is moved, so a smaller limit cannot leave it past the end.
	let length = $derived(Math.min(96, maxNewTokens));
	let active = $derived(edits.interventions);

	function name(edit: Intervention): string {
		const label = edit.vector_id ? steering.label(edit.vector_id) : undefined;
		return `${editTarget(edit, label)} ${editEffect(edit)}`;
	}

	function send() {
		const turn = said;
		if (!turn.trim() || chat.running) return;
		said = '';
		void chat.send(turn, edits.interventions, length);
	}

	function running(exchange: Exchange): boolean {
		return chat.running && exchange === chat.exchanges.at(-1);
	}
</script>

{#snippet steeringLine(interventions: Intervention[])}
	{#if interventions.length === 0}
		<span class="text-muted-foreground">No edits: both replies are the model's own.</span>
	{:else}
		{#each interventions as edit, index (index)}
			<span class="rounded-md border border-border px-1.5 py-0.5 font-mono text-[11px]">
				{name(edit)}
			</span>
		{/each}
	{/if}
{/snippet}

<div class="flex h-full min-h-0 flex-col">
	<div
		class="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2 text-xs"
		aria-label="Steering the next reply"
	>
		<span class="font-medium">Steering the next reply</span>
		{@render steeringLine(active)}
		<div class="ml-auto flex items-center gap-1">
			<Button
				variant="ghost"
				size="sm"
				disabled={active.length === 0}
				onclick={() => edits.replace([])}
				title="Remove every edit, so the next reply is the model's own"
			>
				Clear edits
			</Button>
			<Button
				variant="ghost"
				size="sm"
				disabled={chat.exchanges.length === 0}
				onclick={() => chat.clear()}
			>
				New conversation
			</Button>
		</div>
	</div>

	<div class="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto p-4" aria-live="polite">
		{#if chat.exchanges.length === 0}
			<div class="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
				<span
					aria-hidden="true"
					class="grid size-12 place-items-center rounded-xl border border-border"
					style:background-image="linear-gradient(135deg, var(--color-card), color-mix(in oklab, var(--color-lab-positive) 35%, var(--color-card)))"
				>
					<MessagesSquare size={18} class="text-[var(--color-lab-positive)]" />
				</span>
				<p class="text-sm font-medium">Talk to the steered model</p>
				<p class="max-w-md text-xs leading-relaxed text-muted-foreground">
					Add edits in the list, then chat. Every token of each reply is written with them, and
					the model's own reply to the same conversation is beside it. The steered reply is the
					one the next turn sees. Change or clear the edits between turns.
				</p>
			</div>
		{/if}

		{#each chat.exchanges as exchange (exchange.key)}
			{@const marked = markTokens(exchange.view)}
			<article class="flex flex-col gap-3">
				<p
					class="max-w-[48rem] self-end whitespace-pre-wrap rounded-lg bg-muted px-4 py-2 text-sm"
				>
					{exchange.said}
				</p>
				<p class="flex flex-wrap items-center gap-1.5 text-xs">
					<span class="text-muted-foreground">Steered with</span>
					{@render steeringLine(exchange.steering)}
				</p>
				{#if exchange.view.failure}
					<LabFailure failure={exchange.view.failure} />
				{/if}
				{#if running(exchange) && exchange.view.ahead}
					<p class="flex items-center gap-2 text-xs text-muted-foreground" role="status">
						<Spinner size={12} aria-hidden="true" />
						Waiting behind {exchange.view.ahead}
						{exchange.view.ahead === 1 ? 'run' : 'runs'}…
					</p>
				{/if}
				<div class="grid gap-4 xl:grid-cols-2">
					<MarkedAnswer
						title="Steered"
						tokens={marked.edited}
						running={running(exchange) && exchange.view.edited.length > 0}
					/>
					<MarkedAnswer
						title="The model’s own"
						tokens={marked.baseline}
						running={running(exchange) &&
							exchange.view.edited.length === 0 &&
							!exchange.view.ahead}
					/>
				</div>
				{#if !running(exchange) && !chat.kept(exchange)}
					<p class="text-xs text-muted-foreground">
						Not part of the conversation: the next turn is sent without this one.
					</p>
				{/if}
			</article>
		{/each}
	</div>

	<form
		class="grid gap-3 border-t border-border p-4 md:grid-cols-[minmax(0,1fr)_12rem_auto] md:items-end"
		onsubmit={(event) => {
			event.preventDefault();
			send();
		}}
	>
		<Textarea
			bind:value={said}
			label="Your turn"
			description="Enter sends; Shift+Enter starts a new line."
			placeholder="Tell me about the ocean."
			rows={2}
			autoresize
			onkeydown={(event: KeyboardEvent) => {
				if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
					event.preventDefault();
					send();
				}
			}}
		/>
		<Slider
			bind:value={length}
			min={1}
			max={maxNewTokens}
			step={1}
			label="Reply length"
			format={(value) => `${value} tokens`}
		/>
		{#if chat.running}
			<Button type="button" variant="outline" onclick={() => chat.stop()} class="gap-1.5">
				<Square size={14} aria-hidden="true" />
				Stop
			</Button>
		{:else}
			<Button type="submit" disabled={!said.trim()} class="gap-1.5">
				<Send size={14} aria-hidden="true" />
				Send
			</Button>
		{/if}
	</form>
</div>
