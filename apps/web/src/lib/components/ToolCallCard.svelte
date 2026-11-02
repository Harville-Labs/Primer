<script lang="ts">
	import { Alert, Badge, Button } from '@sivir-ui/svelte';
	import type { ToolCallSummary, ToolPhase } from '#lib/api/types.ts';

	let { call }: { call: ToolCallSummary } = $props();

	//: Held until the stream says the call was decided, so a second click
	//: cannot send a second decision while the first is on its way.
	let sending = $state(false);
	let refusal = $state<string | null>(null);

	const phases: Record<ToolPhase, { label: string; variant: 'success' | 'destructive' | 'warning' | 'secondary' }> =
		{
			requested: { label: 'Waiting for you', variant: 'warning' },
			approved: { label: 'Approved', variant: 'secondary' },
			running: { label: 'Running', variant: 'secondary' },
			completed: { label: 'Done', variant: 'success' },
			denied: { label: 'Denied', variant: 'destructive' },
			failed: { label: 'Failed', variant: 'destructive' },
			expired: { label: 'Expired', variant: 'destructive' }
		};

	/**
	 * Send the decision. The card does not change itself on success: the
	 * stream that asked carries the decided call back, and that one copy is
	 * what the card shows. Changing it here as well would give it two
	 * sources that can disagree.
	 */
	async function decide(decision: 'approve' | 'deny') {
		sending = true;
		refusal = null;
		try {
			const response = await fetch(`/chat/tools/${encodeURIComponent(call.id)}`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ decision })
			});
			if (!response.ok) {
				const problem = (await response.json().catch(() => null)) as {
					title?: string;
					detail?: string | null;
				} | null;
				refusal = problem?.detail ?? problem?.title ?? 'The decision was not accepted.';
				sending = false;
			}
		} catch {
			refusal = 'Primer could not be reached. Try again.';
			sending = false;
		}
	}
</script>

<!--
  A tool call the model asked for, inside the answer it belongs to. The
  arguments are shown in full because they are what is being consented to:
  approving "search the web" without seeing the query is not consent.
-->
<div class="my-2 rounded-md border border-border bg-muted/40 p-3 text-sm">
	<div class="flex items-center gap-2">
		<span class="font-mono text-xs">{call.server_name} · {call.tool_name}</span>
		<Badge variant={phases[call.phase].variant}>{phases[call.phase].label}</Badge>
	</div>

	{#if call.arguments && Object.keys(call.arguments).length > 0}
		<pre
			class="mt-2 max-h-40 overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] font-mono text-xs
				text-muted-foreground">{JSON.stringify(call.arguments, null, 2)}</pre>
	{/if}

	{#if call.phase === 'requested'}
		<div class="mt-2 flex gap-2">
			<Button size="sm" disabled={sending} onclick={() => decide('approve')}>Run it</Button>
			<Button size="sm" variant="ghost" disabled={sending} onclick={() => decide('deny')}>
				Deny
			</Button>
		</div>
	{/if}

	{#if refusal}
		<Alert.Root variant="error" class="mt-2">
			<Alert.Description>{refusal}</Alert.Description>
		</Alert.Root>
	{/if}

	{#if call.output}
		<!-- Bounded by the server; scrolled here so a long result does not bury the answer. -->
		<pre
			class="mt-2 max-h-48 overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] border-l
				border-border pl-3 font-mono text-xs">{call.output}</pre>
	{/if}
</div>
