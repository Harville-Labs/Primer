<script lang="ts">
	import { Badge } from '@sivir-ui/svelte';
	import type { IngestionStatus } from '#lib/api/types.ts';
	import { describeStatus } from '#lib/status.ts';

	interface Props {
		status: IngestionStatus;
		detail?: string | null;
	}

	let { status, detail = null }: Props = $props();
	let described = $derived(describeStatus(status, detail));

	const variants = {
		ready: 'success',
		problem: 'destructive',
		pending: 'warning'
	} as const;
</script>

<!--
  Badge and explanation on one line. The badge is the scannable part and the
  detail is the part you read once something looks wrong, so they belong
  next to each other rather than stacked into double-height rows.
-->
<span class="flex items-center gap-2">
	<Badge variant={variants[described.tone]}>{described.label}</Badge>
	<span class="truncate text-xs text-muted-foreground">{described.detail}</span>
</span>
