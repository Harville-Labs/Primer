<script lang="ts">
	import { ExternalLink } from '@lucide/svelte';
	import { Alert } from '@sivir-ui/svelte';
	import type { Suggestion } from '#lib/settings/suggestions.ts';

	let { suggestions }: { suggestions: Suggestion[] } = $props();
</script>

<!--
  Each one says what a reader gains before it says what to set: an operator
  deciding whether to add a vision model is deciding about figures, not about
  an environment variable.
-->
<ul class="mt-3 grid gap-2">
	{#each suggestions as suggestion (suggestion.id)}
		<li>
			<Alert.Root variant={suggestion.severity}>
				<Alert.Title>{suggestion.title}</Alert.Title>
				<Alert.Description>
					<p>{suggestion.gain}</p>
					<dl class="mt-2 grid gap-1 text-xs sm:grid-cols-[max-content_1fr] sm:gap-x-3">
						<dt class="text-muted-foreground">Compose</dt>
						<dd class="font-mono">
							{suggestion.compose ? suggestion.compose.join(', ') : 'Not available in Compose'}
						</dd>
						<dt class="text-muted-foreground">Chart</dt>
						<dd class="font-mono">{suggestion.chart.join(', ')}</dd>
					</dl>
					<a
						href={suggestion.docs}
						target="_blank"
						rel="noreferrer"
						class="mt-2 inline-flex items-center gap-1 text-xs underline underline-offset-2"
					>
						How to set it up
						<ExternalLink class="size-3" aria-hidden="true" />
					</a>
				</Alert.Description>
			</Alert.Root>
		</li>
	{/each}
</ul>
