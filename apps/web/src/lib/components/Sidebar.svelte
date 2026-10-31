<script lang="ts">
	import { page } from '$app/state';
	import { ChevronRight, Library, MessageSquare, Search, Settings, SquarePen } from '@lucide/svelte';
	import { Shortcut } from '@sivir-ui/svelte';
	import { untrack } from 'svelte';
	import type {
		ConversationSummary,
		DeploymentCapabilities,
		LibrarySummary,
		Principal,
	} from '#lib/api/types.ts';
	import AuthModeWarning from '#lib/components/AuthModeWarning.svelte';
	import ConversationList from '#lib/components/ConversationList.svelte';
	import ThemeToggle from '#lib/components/ThemeToggle.svelte';

	interface Props {
		libraries: LibrarySummary[];
		//: Listed here rather than on the chat screen, so history is one rail
		//: rather than a second one that only exists on one page.
		conversations: ConversationSummary[];
		capabilities: DeploymentCapabilities;
		//: Null only when Control could not be reached for it; the frame still
		//: renders rather than losing navigation over a missing name.
		principal: Principal | null;
		onsearch?: () => void;
		/** Called after any navigation, so the mobile drawer can close itself. */
		onnavigate?: () => void;
		/**
		 * Whether this copy registers the search key. Shortcut binds a window
		 * handler and clicks the control it sits in, so exactly one copy may
		 * carry it; the drawer's copy passes false and the always-mounted rail
		 * keeps it, which is what makes the key work at every width.
		 */
		bindShortcut?: boolean;
	}

	let {
		libraries,
		conversations,
		capabilities,
		principal,
		onsearch,
		onnavigate,
		bindShortcut = true,
	}: Props = $props();

	// A name over an email over the bare subject: the display name is what a
	// person picked to be called, the email is at least recognizable, and the
	// subject is a last resort that still says something rather than nothing.
	let identity = $derived(principal?.display_name || principal?.email || principal?.subject);

	// Prefix rather than equality, so a document list keeps Libraries lit.
	function inSection(href: string): boolean {
		return page.url.pathname === href || page.url.pathname.startsWith(`${href}/`);
	}

	//: Which section's list is showing. Both can be open at once - this is a
	//: disclosure, not a set of tabs - but only one of them is what you are
	//: looking at, which is what the effect below is for.
	//:
	//: Seeded from the current path rather than from a fixed pair, because
	//: effects do not run while the server renders: a hardcoded default ships
	//: the wrong section open in the HTML and folds it away again on
	//: hydration, which is a visible flinch on every first load.
	let expanded = $state(sectionsFor(page.url.pathname));

	function sectionsFor(path: string) {
		return {
			'/libraries': path === '/libraries' || path.startsWith('/libraries/'),
			'/chat': path === '/chat' || path.startsWith('/chat/'),
		};
	}

	// The section you are in opens, and the one you left folds away. Two long
	// lists in a rail this narrow means neither can be read without
	// scrolling, and the one worth reading is the one you are working in.
	// Toggling by hand still holds until the next navigation, because this
	// only runs when the path actually changes.
	$effect(() => {
		const path = page.url.pathname;
		untrack(() => (expanded = sectionsFor(path)));
	});

	let sections = $derived(
		[
			{
				href: '/libraries',
				label: 'Libraries',
				icon: Library,
				count: libraries.length,
				show: true,
			},
			{
				href: '/chat',
				label: 'Chat',
				icon: MessageSquare,
				count: conversations.length,
				show: capabilities.chat_available,
			},
		].filter((section) => section.show)
	);
</script>

<div class="flex h-full min-h-0 flex-col gap-4 px-3 py-4">
	<a
		href="/libraries"
		onclick={onnavigate}
		class="px-2 text-[15px] font-semibold tracking-[-0.02em] focus-visible:outline-none
			focus-visible:shadow-[var(--focus-ring)]"
	>
		Primer
	</a>

	<!--
	  Shaped like the field it opens rather than like a button, and it prints
	  the key that opens it. A palette nobody knows the shortcut for is a
	  palette nobody uses, and the shortcut is the reason it is worth having.
	-->
	<button
		type="button"
		onclick={onsearch}
		class="flex items-center gap-2 rounded-md border border-border bg-card px-2.5 py-1.5
			text-left text-sm text-muted-foreground transition-colors hover:bg-field-hover
			focus-visible:outline-none focus-visible:shadow-[var(--focus-ring)]"
	>
		<Search size={14} aria-hidden="true" />
		<span class="flex-1">Search</span>
		{#if bindShortcut}
			<Shortcut shortcut="cmd+k" class="pointer-events-none" />
		{/if}
	</button>

	<!--
	  Navigation and content in one rail: each section is a link to its
	  screen, and under it the things on that screen. Which library or which
	  conversation you are in is the context for everything else, so
	  switching should not mean navigating back to a list first.

	  The lists live behind a disclosure rather than being stacked in full,
	  because two of them at once in a rail this narrow means neither can be
	  read without scrolling past the other.
	-->
	<nav
		aria-label="Main"
		class="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto overscroll-contain"
	>
		{#each sections as section (section.href)}
			{@const active = inSection(section.href)}
			{@const open = expanded[section.href as '/libraries' | '/chat']}
			<div class="flex min-h-0 flex-col">
				<div class="flex items-center gap-0.5">
					<!--
					  The chevron is its own control, so opening a section and
					  going to it stay separate: a rail that navigated every
					  time you peeked at a list would be a rail you cannot
					  browse.
					-->
					<button
						type="button"
						onclick={() =>
							(expanded = { ...expanded, [section.href]: !open })}
						aria-expanded={open}
						aria-label="{open ? 'Collapse' : 'Expand'} {section.label}"
						class="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted
							hover:text-foreground focus-visible:outline-none
							focus-visible:shadow-[var(--focus-ring)]"
					>
						<ChevronRight
							size={13}
							aria-hidden="true"
							class="transition-transform duration-150 motion-reduce:transition-none
								{open ? 'rotate-90' : ''}"
						/>
					</button>

					<a
						href={section.href}
						onclick={onnavigate}
						aria-current={active ? 'page' : undefined}
						class="flex min-w-0 flex-1 items-center gap-2.5 rounded-md px-2 py-1.5 text-sm
							transition-colors focus-visible:outline-none
							focus-visible:shadow-[var(--focus-ring)]
							{active
							? 'bg-secondary font-medium text-foreground'
							: 'text-muted-foreground hover:bg-muted hover:text-foreground'}"
					>
						<section.icon size={15} aria-hidden="true" class="shrink-0" />
						<span class="flex-1 truncate">{section.label}</span>
						<!--
						  Kept visible while the section is folded, because a
						  collapsed list should still say how much is in it.
						-->
						{#if !open && section.count > 0}
							<span class="font-mono text-[11px] tabular-nums text-muted-foreground">
								{section.count}
							</span>
						{/if}
					</a>

					{#if section.href === '/chat'}
						<!--
						  The way to a blank page, beside the section it starts
						  one in. It is an ordinary link, so it is also a
						  bookmark and a new tab.
						-->
						<a
							href="/chat"
							onclick={onnavigate}
							title="New chat"
							class="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted
								hover:text-foreground focus-visible:outline-none
								focus-visible:shadow-[var(--focus-ring)]"
						>
							<SquarePen size={14} aria-hidden="true" />
							<span class="sr-only">New chat</span>
						</a>
					{/if}
				</div>

				{#if open}
					<div class="ml-3 mt-0.5 min-h-0 border-l border-border pl-1.5">
						{#if section.href === '/libraries'}
							{#if libraries.length === 0}
								<p class="px-2.5 py-1 text-xs text-muted-foreground">
									No libraries yet.
								</p>
							{/if}
							<ul class="-mx-1 min-h-0 space-y-0.5 overflow-y-auto overscroll-contain px-1">
								{#each libraries as library (library.id)}
									{@const current = page.url.pathname === `/libraries/${library.id}`}
									<li>
										<a
											href="/libraries/{library.id}"
											onclick={onnavigate}
											aria-current={current ? 'page' : undefined}
											class="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm
												transition-colors focus-visible:outline-none
												focus-visible:shadow-[var(--focus-ring)]
												{current
												? 'bg-secondary font-medium text-foreground'
												: 'text-muted-foreground hover:bg-muted hover:text-foreground'}"
										>
											<span class="min-w-0 flex-1 truncate" title={library.name}>
												{library.name}
											</span>
											<span
												class="font-mono text-[11px] tabular-nums text-muted-foreground"
											>
												{library.document_count}
											</span>
										</a>
									</li>
								{/each}
							</ul>
						{:else}
							<ConversationList {conversations} {libraries} />
						{/if}
					</div>
				{/if}
			</div>
		{/each}
	</nav>

	<div class="flex flex-col gap-2 border-t border-border px-1 pt-3">
		<AuthModeWarning authEnabled={capabilities.auth_enabled} />

		{#if capabilities.is_admin}
			<!--
			  Only for administrators, and only because Control says so - the
			  browser cannot see the group policy. Hiding it is a courtesy;
			  every route behind it checks again.
			-->
			<a
				href="/settings"
				onclick={onnavigate}
				aria-current={page.url.pathname === '/settings' ? 'page' : undefined}
				class="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors
					focus-visible:outline-none focus-visible:shadow-[var(--focus-ring)]
					{page.url.pathname === '/settings'
					? 'bg-secondary font-medium text-foreground'
					: 'text-muted-foreground hover:bg-muted hover:text-foreground'}"
			>
				<Settings size={15} aria-hidden="true" />
				Settings
			</a>
		{/if}

		{#if identity}
			<!--
			  Who "Signed in" below actually refers to. Without it the badge
			  says a session exists but never who is in it - the one thing a
			  shared deployment most needs to show.
			-->
			<p class="truncate px-1.5 text-sm text-foreground" title={identity}>{identity}</p>
		{/if}

		<div class="flex items-center justify-between">
			<span
				class="px-1.5 font-mono text-[10px] uppercase tracking-[0.09em] text-muted-foreground"
			>
				{capabilities.auth_enabled ? 'Signed in' : 'Local only'}
			</span>
			<ThemeToggle />
		</div>
	</div>
</div>
