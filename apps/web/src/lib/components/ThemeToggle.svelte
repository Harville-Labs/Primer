<script lang="ts">
	import { Monitor, Moon, Sun } from '@lucide/svelte';
	import { DropdownMenu } from '@sivir-ui/svelte';
	import { applyMode, isMode, type Mode, readStoredMode, storeMode } from '#lib/theme/mode.ts';

	// Starts at the server's assumption and corrects on mount: the stored
	// choice lives in the browser, so the server cannot render it.
	let mode = $state<Mode>('system');

	$effect(() => {
		mode = readStoredMode();
	});

	// While following the system, keep following it. Someone whose laptop
	// dims at sunset expects this to dim with it, not on next reload.
	$effect(() => {
		const media = window.matchMedia('(prefers-color-scheme: dark)');
		const sync = () => applyMode(mode);
		media.addEventListener('change', sync);
		return () => media.removeEventListener('change', sync);
	});

	function choose(next: string) {
		if (!isMode(next)) return;
		mode = next;
		storeMode(next);
		applyMode(next);
	}

	const options = [
		{ value: 'light', label: 'Light', icon: Sun },
		{ value: 'dark', label: 'Dark', icon: Moon },
		{ value: 'system', label: 'System', icon: Monitor }
	] as const;

	let active = $derived(options.find((option) => option.value === mode) ?? options[2]);
</script>

<DropdownMenu.Root>
	<!--
	  The label names the current setting rather than just "Theme", because
	  the icon is the only thing saying which one is on and a screen reader
	  gets nothing from it.
	-->
	<DropdownMenu.Trigger variant="ghost" size="icon" aria-label="Theme: {active.label}">
		<active.icon size={16} aria-hidden="true" />
	</DropdownMenu.Trigger>
	<DropdownMenu.Content>
		<!--
		  A radio group, not three buttons: this is one choice out of three,
		  and the group is what tells assistive software which is selected.
		-->
		<DropdownMenu.RadioGroup value={mode} onValueChange={choose}>
			{#each options as option (option.value)}
				<DropdownMenu.RadioItem value={option.value}>
					<span class="flex items-center gap-2">
						<option.icon size={15} aria-hidden="true" />
						{option.label}
					</span>
				</DropdownMenu.RadioItem>
			{/each}
		</DropdownMenu.RadioGroup>
	</DropdownMenu.Content>
</DropdownMenu.Root>
