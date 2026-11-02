<script lang="ts">
	import { TriangleAlert } from '@lucide/svelte';
	import { Composer, Input, Select, Slider, Textarea } from '@sivir-ui/svelte';
	import type { Snippet } from 'svelte';
	import type { Problem } from '#lib/server/problem.ts';
	import type { Generator } from '#lib/studio/generator.svelte.ts';

	/**
	 * The prompt and every setting the picked model or template takes, and
	 * nothing else. Generate and Edit differ only in the words and in what
	 * an edit adds under the model.
	 */
	interface Props {
		generator: Generator;
		//: Why Chat gave the page nothing to pick from, in its own words.
		problem: Problem | null;
		//: Said when nothing here can do what this tab does.
		none: string;
		prompt: { label: string; placeholder: string };
		onsubmit: () => void;
		children?: Snippet;
	}

	let { generator, problem, none, prompt, onsubmit, children }: Props = $props();

	let offer = $derived(generator.offer);
</script>

<div class="flex h-full min-h-0 flex-col gap-5 px-4 py-4">
	<section aria-labelledby="model-heading" class="flex flex-col gap-2">
		<h2
			id="model-heading"
			class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
		>
			Model
		</h2>
		{#if generator.offers.length > 0}
			<Select.Root value={generator.offerKey} onValueChange={(key) => generator.pick(key)}>
				<Select.Trigger class="h-9 w-full text-xs" disabled={generator.running} aria-label="Model or template">
					<!-- As in Lab: Select.Value is blank until the menu opens. -->
					<span class="truncate">{offer ? `${offer.model} · ${offer.providerName}` : 'Pick one'}</span>
				</Select.Trigger>
				<Select.Content>
					{#each generator.offers as each (each.key)}
						<Select.Item value={each.key} class="text-xs">{each.model} · {each.providerName}</Select.Item>
					{/each}
				</Select.Content>
			</Select.Root>
			{#if offer?.api === 'comfyui'}
				<p class="text-[11px] leading-relaxed text-muted-foreground">
					A template your operator set up. Only the inputs it was given are offered here.
				</p>
			{/if}
		{:else if problem}
			<p role="alert" class="flex gap-2 text-xs text-muted-foreground">
				<TriangleAlert size={14} aria-hidden="true" class="mt-px shrink-0 text-warning" />
				<span><span class="font-medium text-foreground">{problem.title}.</span> {problem.detail}</span>
			</p>
		{:else}
			<p class="text-xs leading-relaxed text-muted-foreground">
				{none}
			</p>
		{/if}
	</section>

	{@render children?.()}

	<section aria-labelledby="prompt-heading" class="flex flex-col gap-2">
		<h2
			id="prompt-heading"
			class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
		>
			{prompt.label}
		</h2>
		<Composer.Root
			bind:value={generator.prompt}
			disabled={!offer || generator.running}
			onSubmit={onsubmit}
		>
			<Composer.Input placeholder={prompt.placeholder} aria-label={prompt.label} />
			<Composer.Toolbar>
				<Composer.Actions></Composer.Actions>
				<Composer.Submit />
			</Composer.Toolbar>
		</Composer.Root>
		{#if offer?.negativePrompt}
			<Textarea
				bind:value={generator.negativePrompt}
				label="Avoid"
				placeholder="Blur, text, watermarks."
				rows={2}
				autoresize
				disabled={generator.running}
			/>
		{/if}
	</section>

	{#if offer}
		<section aria-labelledby="settings-heading" class="flex flex-col gap-3">
			<h2
				id="settings-heading"
				class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
			>
				Settings
			</h2>
			{#if offer.sizes.length > 1}
				<Select.Root bind:value={generator.size}>
					<Select.Trigger class="h-9 w-full font-mono text-xs" disabled={generator.running} aria-label="Size">
						<span>{generator.size}</span>
					</Select.Trigger>
					<Select.Content>
						{#each offer.sizes as size (size)}
							<Select.Item value={size} class="font-mono text-xs">{size}</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
			{:else}
				<p class="font-mono text-xs text-muted-foreground">{generator.size}</p>
			{/if}
			{#if offer.maxCount > 1}
				<Slider
					bind:value={generator.count}
					min={1}
					max={offer.maxCount}
					step={1}
					label="Images"
					disabled={generator.running}
				/>
			{/if}
			{#if offer.steps}
				<Slider
					value={generator.steps ?? 30}
					onValueChange={(value: number) => (generator.steps = value)}
					min={1}
					max={150}
					step={1}
					label="Steps"
					disabled={generator.running}
				/>
			{/if}
			{#if offer.seed}
				<Input
					value={generator.seed?.toString() ?? ''}
					oninput={(event: Event) => {
						const text = (event.currentTarget as HTMLInputElement).value.trim();
						const seed = Number(text);
						generator.seed = text && Number.isInteger(seed) && seed >= 0 ? seed : null;
					}}
					inputmode="numeric"
					placeholder="Random"
					aria-label="Seed"
					class="h-9 font-mono text-xs"
					disabled={generator.running}
				/>
				<p class="text-[11px] leading-relaxed text-muted-foreground">
					Leave it empty for a new one each run. The one used is kept with the image.
				</p>
			{/if}
		</section>
	{/if}
</div>
