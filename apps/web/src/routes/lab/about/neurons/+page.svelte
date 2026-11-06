<script lang="ts">
	import AboutIntro from '#lib/components/lab/about/AboutIntro.svelte';
	import Figure from '#lib/components/lab/about/Figure.svelte';
	import GraphFigure from '#lib/components/lab/about/neurons/GraphFigure.svelte';
	import LensGridFigure from '#lib/components/lab/about/neurons/LensGridFigure.svelte';
	import NeuronFigure from '#lib/components/lab/about/neurons/NeuronFigure.svelte';
	import ResidualFigure from '#lib/components/lab/about/neurons/ResidualFigure.svelte';
	import SaeFigure from '#lib/components/lab/about/neurons/SaeFigure.svelte';
	import SteeringFigure from '#lib/components/lab/about/neurons/SteeringFigure.svelte';
	import References, { type Reference } from '#lib/components/lab/about/References.svelte';
	import Section from '#lib/components/lab/about/Section.svelte';

	const contents = [
		{ id: 'reading', title: 'One reading, many layers' },
		{ id: 'lens', title: 'The logit lens' },
		{ id: 'neurons', title: 'Neurons' },
		{ id: 'features', title: 'Features' },
		{ id: 'interventions', title: 'Changing what the model does' },
		{ id: 'graphs', title: 'Attribution graphs' },
		{ id: 'caveats', title: 'What it cannot tell you' },
		{ id: 'references', title: 'References' },
	];

	const references: Reference[] = [
		{
			authors: 'nostalgebraist',
			year: 2020,
			title: 'interpreting GPT: the logit lens',
			href: 'https://www.lesswrong.com/posts/AcKRB8wDpdaN6v6ru/interpreting-gpt-the-logit-lens',
			note: 'Reading an intermediate layer through the output unembedding.',
		},
		{
			authors: 'Elhage et al.',
			year: 2021,
			title: 'A Mathematical Framework for Transformer Circuits',
			href: 'https://transformer-circuits.pub/2021/framework/index.html',
			note: 'The residual stream as the channel every layer reads from and writes to.',
		},
		{
			authors: 'Bricken et al.',
			year: 2023,
			title: 'Towards Monosemanticity',
			href: 'https://transformer-circuits.pub/2023/monosemantic-features/index.html',
			note: 'Superposition, and sparse autoencoders as a way out of it.',
		},
		{
			authors: 'Gao et al.',
			year: 2024,
			title: 'Scaling and evaluating sparse autoencoders',
			href: 'https://arxiv.org/abs/2406.04093',
			note: 'TopK sparse autoencoders, the kind Lab loads.',
		},
		{
			authors: 'Turner et al.',
			year: 2023,
			title: 'Steering Language Models With Activation Engineering',
			href: 'https://arxiv.org/abs/2308.10248',
			note: 'Adding a direction to the residual stream to change behaviour.',
		},
		{
			authors: 'Rimsky et al.',
			year: 2023,
			title: 'Steering Llama 2 via Contrastive Activation Addition',
			href: 'https://arxiv.org/abs/2312.06681',
			note: 'Steering vectors as a difference of means over contrasting prompts.',
		},
		{
			authors: 'Arditi et al.',
			year: 2024,
			title: 'Refusal in Language Models Is Mediated by a Single Direction',
			href: 'https://arxiv.org/abs/2406.11717',
			note: 'One difference-of-means direction that a model relies on to refuse.',
		},
		{
			authors: 'Ameisen et al.',
			year: 2025,
			title: 'Circuit Tracing: Revealing Computational Graphs in Language Models',
			href: 'https://transformer-circuits.pub/2025/attribution-graphs/methods.html',
			note: 'Transcoders and attribution graphs, which Lab builds with circuit-tracer.',
		},
	];
</script>

<svelte:head>
	<title>How Neurons works · Lab · Primer</title>
</svelte:head>

<div class="flex flex-col gap-12">
	<AboutIntro tool="neurons" {contents}>
		Neurons opens up the model Lab runs and shows what happens inside it while it reads a prompt:
		what each layer would predict if the model stopped there, which neurons fire hardest, and
		which learned features are active. You can then change one of those things and see whether
		the answer changes with it.
	</AboutIntro>

	<Section id="reading" title="One reading, many layers">
		<p>
			A language model turns each token into a vector and passes that vector up through a stack
			of layers. Each layer has an attention block, which moves information between tokens, and
			an MLP block, which works on each token by itself. Neither replaces the vector: each adds
			its output to it. The running sum is called the <em>residual stream</em>, and it is the
			only way one layer can tell the next anything.
		</p>
		<Figure
			number={1}
			title="The residual stream"
			caption="Each block reads the stream and adds to it. After every layer, Lab reads the stream and asks the model's own output layer what it would predict from there. The readings shown are illustrative."
		>
			<ResidualFigure />
		</Figure>
		<p>
			When you press Inspect, Lab runs the prompt through the model once and records, at every
			layer and every token: the strongest MLP neurons, the residual stream after the layer and
			its size, and the five tokens the logit lens ranks highest. All of it comes from that one
			pass, so every cell in the grid describes the same computation. Special tokens such as the
			chat template's markers are left out of the grid.
		</p>
		<p>
			The grid can be coloured three ways. <strong>Lens</strong> is how sure the lens is of its
			top token. <strong>Norm</strong> is the size of the residual stream, on a log scale because
			it grows by orders of magnitude from first layer to last. <strong>Neuron</strong> is the
			strongest neuron at that cell, signed, so the two colours tell a large positive activation
			from a large negative one.
		</p>
	</Section>

	<Section id="lens" title="The logit lens">
		<p>
			A model's last step multiplies the final residual stream by an <em>unembedding</em> matrix
			to get a score for every token in its vocabulary. The logit lens applies that same last step
			to the stream at an earlier layer. It answers a narrow question: if the model had to answer
			now, with what it has worked out so far, what would it say?
		</p>
		<Figure
			number={2}
			title="Reading the lens across a prompt"
			caption="Rows are layers, earliest at the bottom; columns are tokens. Darker cells are ones where the lens is more certain of its top guess, shown in each cell. Illustrative values: in a real model the answer often appears in the middle layers and firms up near the top."
		>
			<LensGridFigure />
		</Figure>
		<p>
			Early layers usually produce guesses that look like noise or like the previous token. A
			layer where a sensible answer first appears is a hint about where the model resolved it.
			That is a hint and not a measurement: the unembedding was trained to read the last layer,
			and earlier layers may hold the answer in a form it cannot read.
		</p>
	</Section>

	<Section id="neurons" title="Neurons">
		<p>
			In the gated MLPs that Qwen, Llama, and most current models use, the input is projected up
			twice. One copy passes through an activation function and gates the other, and the product
			is projected back down to the width of the stream. Lab reads the vector just before that
			last projection. Each entry is one neuron, and its value is how strongly that neuron writes
			its own direction into the stream.
		</p>
		<Figure
			number={3}
			title="Where a neuron is read"
			caption="Lab reads the product of the gate and the up-projection, the input to down_proj. It keeps the neurons with the largest magnitude at each token, positive or negative, up to 32."
		>
			<NeuronFigure />
		</Figure>
		<p>
			A neuron that fires on one clear thing is rare. Most fire on many unrelated things, because
			a model has more concepts to represent than it has neurons and stores them in overlapping
			combinations. This is called <em>superposition</em>, and it is why a neuron's index is a
			place to start looking rather than an explanation.
		</p>
	</Section>

	<Section id="features" title="Features">
		<p>
			A sparse autoencoder (SAE) is a second, much wider model trained to rewrite the residual
			stream as a sum of a few directions out of many thousands. Because it can only use a few at
			a time, each direction tends to mean one thing, and those directions are called features.
			Where an operator has configured SAEs for the model, Lab shows the features active at each
			token beside the neurons.
		</p>
		<Figure
			number={4}
			title="Encoding the stream with a TopK SAE"
			caption="The encoder maps the residual vector to a score for every feature; top-k keeps the largest few and sets the rest to zero. Each kept feature's decoder column is the direction it writes, and their sum approximates the original vector."
		>
			<SaeFigure />
		</Figure>
		<p>
			Lab loads TopK SAEs trained on the residual stream after a layer. Encoding is an affine map
			followed by keeping the top k values, computed in 32-bit floats whatever precision the model
			runs in. Lab does this itself rather than through a library: the operation is two lines, and
			the library would add about thirty packages to the image.
		</p>
	</Section>

	<Section id="interventions" title="Changing what the model does">
		<p>
			Reading the model shows what is correlated with an answer. Changing it shows what the answer
			depends on. Compare generates twice from the same prompt, greedily, so the only difference
			between the runs is the edit you made. You choose where to edit (the residual stream, an MLP
			neuron, the output of an MLP or attention block, or an SAE feature), what to do there (set
			it to zero, to its mean over the prompt, scale it, clamp it, or add to it), and at which
			tokens.
		</p>
		<Figure
			number={5}
			title="A steering vector"
			caption="Run a set of positive prompts and a set of negative ones, take the residual stream at each prompt's last token, and subtract the mean of one set from the mean of the other. Adding a multiple of that difference to a new run pushes it towards the positive set."
		>
			<SteeringFigure />
		</Figure>
		<p>
			A steering vector is built from two short lists of prompts that differ in one respect, such
			as formal and casual, or a request the model answers and one it refuses. Lab takes the
			residual stream after a chosen layer at the last token of each prompt and subtracts the mean
			of the negative set from the mean of the positive set. The result can be added to the stream
			in Compare, scaled by a number you choose.
		</p>
		<p>
			Chat holds a conversation with the edits in place at every token the model writes. Each
			reply is written twice, as Compare does, and the steered one is what the next turn sees, so
			the reply beside it is what the model would have said to the same conversation without the
			edits. Changing or clearing the edits between turns steers only the replies still to come. It
			needs a model with a chat template, which tells the model's turns from yours.
		</p>
	</Section>

	<Section id="graphs" title="Attribution graphs">
		<p>
			An attribution graph traces one prediction back to the prompt. It replaces each layer's MLP
			with a <em>transcoder</em>, a sparse model trained to reproduce what that MLP outputs from
			what it reads, and then measures how much each active feature contributed to each later one
			and to the final answer. Edges that carry little are pruned, leaving the paths that matter.
		</p>
		<Figure
			number={6}
			title="An attribution graph"
			caption="Prompt tokens at the bottom feed features, which feed the output. Thicker edges carry more influence. Here the model finds the answer through an intermediate step, the state Dallas is in, rather than in one jump. Illustrative, after Ameisen et al. (2025)."
		>
			<GraphFigure />
		</Figure>
		<p>
			Lab builds graphs with circuit-tracer in a separate program (ADR 0009), because circuit-tracer
			needs different versions of the libraries Lab is built on, and loads its own copy of the
			model. A graph takes seconds on a GPU and many minutes on a CPU. The prompt is used as plain text,
			without the chat template, and is limited to a short length set by the operator, since the
			cost grows quickly with the number of tokens.
		</p>
	</Section>

	<Section id="caveats" title="What it cannot tell you">
		<ul>
			<li>
				The logit lens reads early layers with a tool trained for the last one. A meaningless
				guess at layer 3 does not mean the model knows nothing there.
			</li>
			<li>
				An SAE is an approximation. What it does not reconstruct, the model still uses, and a
				feature's label is someone's reading of the examples that activate it.
			</li>
			<li>
				An intervention that changes the answer shows that the answer depends on what you
				changed, in this prompt. It does not show that nothing else matters, or that the same
				holds for other prompts.
			</li>
			<li>
				An attribution graph explains the transcoder version of the model, which is close to the
				real one but not the same. Check a graph's story with an intervention.
			</li>
		</ul>
	</Section>

	<Section id="references" title="References">
		<References {references} />
	</Section>
</div>
