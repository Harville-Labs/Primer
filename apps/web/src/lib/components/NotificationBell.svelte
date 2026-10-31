<script lang="ts">
	import { Bell } from '@lucide/svelte';
	import { Popover } from '@sivir-ui/svelte';
	import { markAllRead, notifications, unreadCount } from '#lib/notifications.svelte.ts';

	let open = $state(false);

	function formatWhen(at: number): string {
		const seconds = Math.max(0, Math.round((Date.now() - at) / 1000));
		if (seconds < 60) return 'just now';
		const minutes = Math.round(seconds / 60);
		if (minutes < 60) return `${minutes}m ago`;
		const hours = Math.round(minutes / 60);
		if (hours < 24) return `${hours}h ago`;
		return `${Math.round(hours / 24)}d ago`;
	}
</script>

<!--
  Fixed to the corner rather than in the frame, so it is reachable from
  every screen without competing with each page's own layout for space.
  Read on open rather than on a per-item basis: the badge exists to say
  "something happened while you were away," not to track which one.
-->
<div class="fixed bottom-4 right-4 z-40">
	<Popover.Root bind:open placement="top-end">
		<Popover.Trigger
			variant="ghost"
			size="icon"
			class="relative rounded-full border border-border bg-card shadow-[var(--elevation-float)]"
			aria-label="Notifications"
			onopen={markAllRead}
		>
			<Bell size={16} aria-hidden="true" />
			{#if unreadCount() > 0}
				<span
					class="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center
						rounded-full bg-error px-1 text-[10px] font-medium text-white"
				>
					{unreadCount()}
				</span>
			{/if}
		</Popover.Trigger>

		<Popover.Content class="w-80" surfaceClass="p-0">
			<Popover.Title class="border-b border-border px-3 py-2 text-sm font-medium">
				Notifications
			</Popover.Title>
			<div class="max-h-80 overflow-y-auto overscroll-contain">
				{#if notifications().length === 0}
					<p class="px-3 py-6 text-center text-sm text-muted-foreground">Nothing yet.</p>
				{:else}
					{#each notifications() as entry (entry.id)}
						<div class="border-b border-border px-3 py-2 last:border-0">
							<p class="text-sm text-foreground">{entry.title}</p>
							{#if entry.description}
								<p class="mt-0.5 text-xs text-muted-foreground">{entry.description}</p>
							{/if}
							<p class="mt-1 text-[11px] text-muted-foreground">{formatWhen(entry.at)}</p>
						</div>
					{/each}
				{/if}
			</div>
		</Popover.Content>
	</Popover.Root>
</div>
