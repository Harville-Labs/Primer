<script lang="ts">
	import { goto } from '$app/navigation';
	import { Command } from '@sivir-ui/svelte';
	import type { DeploymentCapabilities, LibrarySummary } from '#lib/api/types.ts';

	interface Props {
		libraries: LibrarySummary[];
		capabilities: DeploymentCapabilities;
		/** Opened by the sidebar's search control, which owns the shortcut. */
		open?: boolean;
	}

	let { libraries, capabilities, open = $bindable(false) }: Props = $props();

	function jump(href: string) {
		open = false;
		void goto(href);
	}
</script>

<Command.Root bind:open>
	<Command.Content label="Search Primer">
		<Command.Search placeholder="Jump to a library, or a page…" />
		<Command.Results>
			<Command.Group heading="Libraries">
				{#each libraries as library (library.id)}
					<Command.Item
						name={library.name}
						callback={() => jump(`/libraries/${library.id}`)}
					>
						<span class="flex w-full items-center justify-between gap-4">
							<span class="truncate">{library.name}</span>
							<span class="font-mono text-xs text-muted-foreground">
								{library.document_count}
							</span>
						</span>
					</Command.Item>
				{/each}
			</Command.Group>

			<Command.Group heading="Go to">
				<Command.Item name="Libraries" callback={() => jump('/libraries')}>
					Libraries
				</Command.Item>
				{#if capabilities.chat_available}
					<Command.Item name="Chat" callback={() => jump('/chat')}>Chat</Command.Item>
				{/if}
			</Command.Group>
		</Command.Results>
	</Command.Content>
</Command.Root>
