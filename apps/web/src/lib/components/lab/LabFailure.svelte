<script lang="ts">
	import { Alert } from '@sivir-ui/svelte';
	import type { Snippet } from 'svelte';
	import type { Failure } from '#lib/lab/failure.ts';

	/**
	 * Why a Lab request did not work, said the same way in every pane.
	 *
	 * `error` is for a request the person made that failed; `warning` is for
	 * Lab itself not being ready, which the person cannot fix from here.
	 * `children` adds what is still on screen despite the failure.
	 */
	interface Props {
		failure: Failure;
		variant?: 'error' | 'warning';
		class?: string;
		children?: Snippet;
	}

	let { failure, variant = 'error', class: className, children }: Props = $props();
</script>

<Alert.Root {variant} class={className}>
	<Alert.Title>{failure.title}</Alert.Title>
	<Alert.Description>{failure.detail}</Alert.Description>
	{@render children?.()}
</Alert.Root>
