<script lang="ts">
	import { Button } from '@sivir-ui/svelte';
	import type { MessageSummary } from '#lib/api/types.ts';
	import CopyAction from '#lib/components/CopyAction.svelte';
	import { copyResponseText, exportResponseMarkdown } from '#lib/export/markdown.ts';

	interface Props {
		message: MessageSummary;
		filenames?: Record<string, string>;
		onshowsources?: () => void;
	}

	let { message, filenames = {}, onshowsources }: Props = $props();

	let markdown = $derived(exportResponseMarkdown(message, filenames));
	let plain = $derived(copyResponseText(message));

	function download() {
		// A file, because an exported answer is usually going into a notes
		// app or a document rather than back into this page.
		const blob = new Blob([markdown], { type: 'text/markdown' });
		const url = URL.createObjectURL(blob);
		const link = document.createElement('a');
		link.href = url;
		link.download = `primer-answer-${message.id.slice(0, 8)}.md`;
		link.click();
		URL.revokeObjectURL(url);
	}
</script>

<div class="flex flex-wrap items-center gap-2">
	<CopyAction text={plain} label="Copy text" />
	<!--
	  Markdown separately, because the two are genuinely different: pasting
	  into a chat wants plain prose, pasting into notes wants the citations.
	  Which is why they have to be told apart at a glance.
	-->
	<CopyAction text={markdown} label="Copy with sources" />
	<Button variant="ghost" size="sm" onclick={download}>Export</Button>
	{#if message.citations.length > 0}
		<Button variant="ghost" size="sm" onclick={onshowsources}>
			{message.citations.length}
			{message.citations.length === 1 ? 'source' : 'sources'}
		</Button>
	{/if}
</div>
