<script lang="ts">
	// Reference points drawn from a correlated two-dimensional Gaussian,
	// with a fixed seed so the figure does not change between loads.
	const centre = { x: 236, y: 128 };
	const sigma = { major: 40, minor: 12 };
	const angle = (-24 * Math.PI) / 180;

	let seed = 11;
	const uniform = () => {
		seed = (seed * 16807) % 2147483647;
		return seed / 2147483647;
	};
	const gaussian = () => Math.sqrt(-2 * Math.log(uniform())) * Math.cos(2 * Math.PI * uniform());
	const place = (along: number, across: number) => ({
		x: centre.x + along * Math.cos(angle) - across * Math.sin(angle),
		y: centre.y + along * Math.sin(angle) + across * Math.cos(angle),
	});
	const reference = Array.from({ length: 140 }, () =>
		place(gaussian() * sigma.major, gaussian() * sigma.minor),
	);

	// The 99th percentile of a two-dimensional Gaussian's Mahalanobis
	// distance is the square root of the chi-squared quantile, about 3.03.
	const radius = Math.sqrt(9.21);
	const euclidean = 72;
	const across = place(0, 54);
	const along = place(100, 0);
	const degrees = (angle * 180) / Math.PI;
</script>

<svg viewBox="0 0 480 256" class="mx-auto block h-auto w-full max-w-lg" role="img" aria-labelledby="outlier-title">
	<title id="outlier-title">Mahalanobis and Euclidean distance from a cloud of reference tokens</title>
	{#each reference as point, index (index)}
		<circle cx={point.x} cy={point.y} r="2.5" class="fill-muted-foreground" fill-opacity="0.45" />
	{/each}

	<circle cx={centre.x} cy={centre.y} r={euclidean} class="fill-none stroke-muted-foreground" stroke-dasharray="3 4" />
	<ellipse
		cx={centre.x}
		cy={centre.y}
		rx={sigma.major * radius}
		ry={sigma.minor * radius}
		transform="rotate({degrees} {centre.x} {centre.y})"
		class="fill-none stroke-lab-positive"
		stroke-width="2"
	/>

	<circle cx={across.x} cy={across.y} r="6" class="fill-lab-negative" />
	<text x={across.x + 10} y={across.y + 4} class="fill-lab-negative text-[11px] font-medium">A: outlier</text>
	<circle cx={along.x} cy={along.y} r="6" class="fill-card stroke-foreground" stroke-width="2" />
	<text x={along.x + 10} y={along.y - 6} class="fill-foreground text-[11px] font-medium">B: ordinary</text>

	<line x1="16" y1="232" x2="36" y2="232" class="stroke-lab-positive" stroke-width="2" />
	<text x="42" y="236" class="fill-muted-foreground text-[10px]">Mahalanobis, 99th percentile</text>
	<line x1="210" y1="232" x2="230" y2="232" class="stroke-muted-foreground" stroke-dasharray="3 4" />
	<text x="236" y="236" class="fill-muted-foreground text-[10px]">Euclidean circle</text>
</svg>
