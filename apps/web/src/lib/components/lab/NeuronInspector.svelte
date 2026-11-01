<script lang="ts">
	import { ScanLine } from '@lucide/svelte';
	import { Button, Spinner, ToggleGroup } from '@sivir-ui/svelte';
	import type { ActivationSite, InterventionSite } from '#lib/api/types.ts';
	import {
		cellColour,
		featuresAt,
		lensAt,
		neuronsAt,
		normByLayer,
		summariseVector,
		visibleToken,
	} from '#lib/lab/heatmap.ts';
	import type { Inspector } from '#lib/lab/inspector.svelte.ts';
	import SignedBars from './SignedBars.svelte';

	/** What the selected cell holds: the lens's guesses, its neurons, its whole vector. */
	interface Props {
		inspector: Inspector;
		//: Given, a neuron or feature row can be added as an edit at the selected layer.
		onedit?: (edit: { site: InterventionSite; layer: number; index: number }) => void;
	}

	let { inspector, onedit }: Props = $props();

	const SITES: { value: ActivationSite; label: string }[] = [
		{ value: 'resid_post', label: 'Residual' },
		{ value: 'mlp_neuron', label: 'Neurons' },
		{ value: 'mlp_out', label: 'MLP out' },
		{ value: 'attn_out', label: 'Attn out' },
	];

	let reading = $derived(inspector.reading);
	let at = $derived(inspector.selected);
	let token = $derived(reading?.tokens[at.token]);
	let lens = $derived(reading ? lensAt(reading, at) : []);
	let neurons = $derived(reading ? neuronsAt(reading, at) : []);
	let features = $derived(reading ? featuresAt(reading, at) : null);
	// The reading's own status, which is the one its features were read with.
	let saes = $derived(reading?.model.saes ?? null);
	let covered = $derived(saes?.layers ?? []);
	let norms = $derived(reading ? normByLayer(reading, at.token) : []);
	let vector = $derived(inspector.vector ? summariseVector(inspector.vector.values) : null);

	// The residual's growth through the layers for this token, as a line.
	// Log-scaled for the same reason the heatmap's norm is.
	let trace = $derived.by(() => {
		if (norms.length < 2) return null;
		const logs = norms.map((norm) => Math.log1p(norm));
		const low = Math.min(...logs);
		const span = Math.max(...logs) - low || 1;
		const point = (value: number, layer: number) =>
			[(layer / (logs.length - 1)) * 100, 28 - ((value - low) / span) * 26] as const;
		const points = logs.map(point);
		return {
			line: points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' '),
			area: `0,30 ${points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ')} 100,30`,
			marker: points[at.layer],
		};
	});

	const number = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 3 });
	const percent = new Intl.NumberFormat(undefined, {
		style: 'percent',
		maximumFractionDigits: 1,
	});
</script>

{#if !reading || !token}
	<div class="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
		<ScanLine size={20} aria-hidden="true" class="text-muted-foreground" />
		<p class="text-sm text-muted-foreground">Run a prompt, then pick a cell to read it.</p>
	</div>
{:else}
	<div class="flex flex-col gap-6 px-4 py-4">
		<header class="flex flex-col gap-2">
			<p class="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
				Layer {at.layer} of {reading.layers.length - 1} · position {at.token}
			</p>
			<p
				class="w-fit max-w-full truncate rounded-md border border-border bg-secondary px-2 py-1
					font-mono text-lg leading-tight {token.special ? 'italic text-muted-foreground' : ''}"
				title={JSON.stringify(token.text)}
			>
				{visibleToken(token.text)}
			</p>
			<p class="text-xs text-muted-foreground">
				Token id <span class="font-mono tabular-nums text-foreground">{token.id}</span>
				{#if token.special}· from the chat template{/if}
			</p>
		</header>

		<section aria-labelledby="lens-heading" class="flex flex-col gap-2">
			<h3 id="lens-heading" class="text-xs font-semibold uppercase tracking-[0.08em]">
				Logit lens
			</h3>
			<p class="text-xs text-muted-foreground">
				What the model would say next if it stopped at this layer.
			</p>
			<ol class="flex flex-col gap-1">
				{#each lens as guess, rank (rank)}
					<!--
					  Ink follows the bar under it: the top guess is drawn deep,
					  so whichever end of its row the bar reaches takes the
					  light ink, and the rest keeps the ordinary one.
					-->
					{@const deep = rank === 0}
					<li class="relative overflow-hidden rounded-[4px] bg-secondary">
						<span
							aria-hidden="true"
							class="absolute inset-y-0 left-0 transition-[width] duration-300 ease-out
								motion-reduce:transition-none"
							style:width="{Math.max(1, guess.probability * 100)}%"
							style:background-color={cellColour(deep ? 0.85 : 0.4)}
						></span>
						<span class="relative flex items-center justify-between gap-3 px-2 py-1">
							<span
								class="truncate font-mono text-xs"
								style:color={deep && guess.probability > 0.25 ? 'var(--color-on-lab)' : undefined}
							>
								{visibleToken(guess.text)}
							</span>
							<span
								class="font-mono text-[11px] tabular-nums
									{deep && guess.probability > 0.9 ? '' : 'text-muted-foreground'}"
								style:color={deep && guess.probability > 0.9 ? 'var(--color-on-lab)' : undefined}
							>
								{percent.format(guess.probability)}
							</span>
						</span>
					</li>
				{/each}
			</ol>
		</section>

		{#if trace}
			<section aria-labelledby="norm-heading" class="flex flex-col gap-2">
				<div class="flex items-baseline justify-between">
					<h3 id="norm-heading" class="text-xs font-semibold uppercase tracking-[0.08em]">
						Residual norm
					</h3>
					<span class="font-mono text-[11px] tabular-nums">
						{number.format(norms[at.layer])}
					</span>
				</div>
				<svg
					viewBox="0 0 100 30"
					preserveAspectRatio="none"
					class="h-14 w-full overflow-visible"
					role="img"
					aria-label="Residual norm at each layer for this token, on a log scale"
				>
					<polygon points={trace.area} fill="var(--color-lab-positive)" opacity="0.12" />
					<polyline
						points={trace.line}
						fill="none"
						stroke="var(--color-lab-positive)"
						stroke-width="1.5"
						vector-effect="non-scaling-stroke"
						stroke-linejoin="round"
					/>
					<line
						x1={trace.marker[0]}
						x2={trace.marker[0]}
						y1="0"
						y2="30"
						stroke="var(--color-foreground)"
						stroke-dasharray="2 2"
						vector-effect="non-scaling-stroke"
						opacity="0.4"
					/>
				</svg>
				<div class="flex justify-between font-mono text-[10px] text-muted-foreground">
					<span>L0</span><span>L{reading.layers.length - 1}</span>
				</div>
			</section>
		{/if}

		<section aria-labelledby="neurons-heading" class="flex flex-col gap-2">
			<h3 id="neurons-heading" class="text-xs font-semibold uppercase tracking-[0.08em]">
				Strongest MLP neurons
			</h3>
			<p class="text-xs text-muted-foreground">
				Of {reading.mlp_width.toLocaleString()} at this layer, by magnitude.
			</p>
			<SignedBars
				rows={neurons}
				prefix="n"
				noun="neuron"
				onadd={onedit && ((index) => onedit({ site: 'mlp_neuron', layer: at.layer, index }))}
			/>
		</section>

		<section aria-labelledby="features-heading" class="flex flex-col gap-2">
			<h3 id="features-heading" class="text-xs font-semibold uppercase tracking-[0.08em]">
				Strongest SAE features
			</h3>
			{#if !saes}
				<p class="text-xs text-muted-foreground">
					No sparse autoencoders are mapped to this model. An operator maps them with
					<code class="font-mono">PRIMER_LAB_SAES</code>.
				</p>
			{:else if saes.state === 'fetching'}
				<p role="status" class="flex items-center gap-2 text-xs text-muted-foreground">
					<Spinner size={12} aria-hidden="true" />
					Downloading {saes.repository}. Run the prompt again once it is ready.
				</p>
			{:else if saes.state === 'failed'}
				<p role="alert" class="text-xs">
					<span class="font-medium text-error">Lab would not open {saes.repository}:</span>
					{saes.error}
				</p>
			{:else if features}
				<p class="text-xs text-muted-foreground">
					Of {saes.width?.toLocaleString()} in
					<span class="font-mono" title={saes.commit ?? undefined}>{saes.repository}</span>,
					the strongest of the {saes.k} that fire.
				</p>
				<SignedBars
					rows={features}
					prefix="f"
					noun="feature"
					onadd={onedit && ((index) => onedit({ site: 'sae_feature', layer: at.layer, index }))}
				/>
			{:else}
				<p class="text-xs text-muted-foreground">
					{saes.repository} has no SAE for layer {at.layer}. It reads
					{covered.length === 1 ? 'layer' : 'layers'}
					{covered.join(', ')}.
				</p>
			{/if}
		</section>

		<section aria-labelledby="vector-heading" class="flex flex-col gap-3">
			<h3 id="vector-heading" class="text-xs font-semibold uppercase tracking-[0.08em]">
				Whole vector
			</h3>
			<ToggleGroup.Root
				type="single"
				value={inspector.site}
				onValueChange={(value) => {
					if (typeof value === 'string') inspector.site = value as ActivationSite;
				}}
				class="flex flex-wrap"
			>
				{#each SITES as site (site.value)}
					<ToggleGroup.Item value={site.value} class="text-xs">{site.label}</ToggleGroup.Item>
				{/each}
			</ToggleGroup.Root>
			<Button
				variant="secondary"
				size="sm"
				disabled={inspector.readingVector}
				onclick={() => inspector.readVector()}
			>
				{#if inspector.readingVector}<Spinner size={12} aria-hidden="true" />{/if}
				Read this cell
			</Button>

			{#if inspector.vectorFailure}
				<p role="alert" class="text-xs text-error">
					<span class="font-medium">{inspector.vectorFailure.title}.</span>
					{inspector.vectorFailure.detail}
				</p>
			{/if}

			{#if vector && inspector.vector}
				<figure class="flex flex-col gap-1.5 {inspector.vector.stale ? 'opacity-50' : ''}">
					<!--
					  A barcode: one stripe per bin, coloured by sign, opaque by
					  size. A vector a few thousand wide is unreadable as numbers
					  and immediately readable as this - the outliers are the
					  dark stripes.
					-->
					<svg
						viewBox="0 0 {vector.bins.length} 1"
						preserveAspectRatio="none"
						class="h-12 w-full rounded-[3px] bg-secondary"
						role="img"
						aria-label="{vector.width} values, from {number.format(vector.low)} to {number.format(
							vector.high
						)}"
					>
						{#each vector.bins as value, index (index)}
							{#if value !== 0}
								<rect
									x={index}
									y="0"
									width="1"
									height="1"
									fill={value < 0 ? 'var(--color-lab-negative)' : 'var(--color-lab-positive)'}
									opacity={Math.abs(value) ** 0.6}
								/>
							{/if}
						{/each}
					</svg>
					<figcaption class="flex flex-col gap-1.5">
						<span
							class="grid grid-cols-3 gap-2 font-mono text-[10px] tabular-nums
								text-muted-foreground"
						>
							<span>{vector.width.toLocaleString()} dims</span>
							<span class="text-center">‖v‖ {number.format(vector.norm)}</span>
							<span class="text-right">
								{number.format(vector.low)} … {number.format(vector.high)}
							</span>
						</span>
						<span class="text-[11px] text-muted-foreground">
							L{inspector.vector.layer} · position {inspector.vector.token} ·
							{SITES.find((site) => site.value === inspector.vector?.site)?.label}
							{#if inspector.vector.stale}· not the selected cell{/if}
						</span>
					</figcaption>
				</figure>
			{/if}
		</section>
	</div>
{/if}
