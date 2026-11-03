<script lang="ts">
	import AboutIntro from '#lib/components/lab/about/AboutIntro.svelte';
	import EntropyFigure from '#lib/components/lab/about/anomalies/EntropyFigure.svelte';
	import OutlierFigure from '#lib/components/lab/about/anomalies/OutlierFigure.svelte';
	import ProbeFigure from '#lib/components/lab/about/anomalies/ProbeFigure.svelte';
	import SpikeFigure from '#lib/components/lab/about/anomalies/SpikeFigure.svelte';
	import SurprisalFigure from '#lib/components/lab/about/anomalies/SurprisalFigure.svelte';
	import Figure from '#lib/components/lab/about/Figure.svelte';
	import References, { type Reference } from '#lib/components/lab/about/References.svelte';
	import Section from '#lib/components/lab/about/Section.svelte';

	const contents = [
		{ id: 'surprisal', title: 'Surprisal' },
		{ id: 'spikes', title: 'Finding the spikes' },
		{ id: 'entropy', title: 'Entropy and low confidence' },
		{ id: 'outliers', title: 'Activation outliers' },
		{ id: 'probes', title: 'Probes' },
		{ id: 'caveats', title: 'What it cannot tell you' },
		{ id: 'references', title: 'References' },
	];

	const references: Reference[] = [
		{
			authors: 'Shannon',
			year: 1948,
			title: 'A Mathematical Theory of Communication',
			note: 'Information content and entropy, measured in bits.',
		},
		{
			authors: 'Iglewicz and Hoaglin',
			year: 1993,
			title: 'How to Detect and Handle Outliers',
			note: 'The modified z-score and its 3.5 cut-off.',
		},
		{
			authors: 'Alon and Kamfonas',
			year: 2023,
			title: 'Detecting Language Model Attacks with Perplexity',
			href: 'https://arxiv.org/abs/2308.14132',
			note: 'Unusually surprising text as a signal of an adversarial prompt.',
		},
		{
			authors: 'Greshake et al.',
			year: 2023,
			title: 'Not what you’ve signed up for: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection',
			href: 'https://arxiv.org/abs/2302.12173',
			note: 'Instructions hidden in retrieved documents, the case Anomalies and Scan look for.',
		},
		{
			authors: 'Ledoit and Wolf',
			year: 2004,
			title: 'A well-conditioned estimator for large-dimensional covariance matrices',
			note: 'The shrunk covariance the outlier reference uses.',
		},
		{
			authors: 'Lee et al.',
			year: 2018,
			title: 'A Simple Unified Framework for Detecting Out-of-Distribution Samples and Adversarial Attacks',
			href: 'https://arxiv.org/abs/1807.03888',
			note: 'Mahalanobis distance on a network’s activations as an out-of-distribution score.',
		},
		{
			authors: 'Alain and Bengio',
			year: 2016,
			title: 'Understanding intermediate layers using linear classifier probes',
			href: 'https://arxiv.org/abs/1610.01644',
			note: 'Linear probes on hidden layers.',
		},
		{
			authors: 'Hewitt and Liang',
			year: 2019,
			title: 'Designing and Interpreting Probes with Control Tasks',
			href: 'https://arxiv.org/abs/1909.03368',
			note: 'Why a probe’s accuracy has to be read against a baseline.',
		},
	];
</script>

<svelte:head>
	<title>How Anomalies works · Lab · Primer</title>
</svelte:head>

<div class="flex flex-col gap-12">
	<AboutIntro tool="anomalies" {contents}>
		Anomalies reads a piece of text through a model and marks the tokens the model did not expect.
		A sentence that does not belong, such as an instruction hidden in a document, tends to surprise
		a model in a way the text around it does not. Anomalies can also measure how far the model's
		internal state strays from ordinary text, and apply a probe you trained to recognise something
		specific.
	</AboutIntro>

	<Section id="surprisal" title="Surprisal">
		<p>
			At each token a language model assigns a probability to every token that could come next.
			Once the next token is known, the model's surprise at it is the negative logarithm of the
			probability it gave: <code>−log₂ p</code>, measured in bits. A token the model gave even odds
			is one bit. One it gave one chance in a thousand is about ten.
		</p>
		<Figure
			number={1}
			title="Probability and surprisal"
			caption="Surprisal grows slowly while the model is fairly sure and steeply as the probability approaches zero, so the tokens that matter stand far above the rest."
		>
			<SurprisalFigure />
		</Figure>
		<p>
			You can score text in two ways. In <strong>text</strong> mode you paste something and every
			token after the first is scored against what came before it; the first has nothing before
			it. In <strong>generate</strong> mode the model writes an answer and each token it wrote is
			scored. The whole text gets one summary number, its perplexity: two to the power of the mean
			surprisal, or roughly how many tokens the model was choosing between on average.
		</p>
		<p>
			The model can be Lab's own or one served through Chat. Lab's model gives the full
			distribution at every token. An endpoint gives only the top 20 logprobs it returns, which is
			enough for surprisal but not for exact entropy, and only endpoints declared to return
			logprobs are offered.
		</p>
	</Section>

	<Section id="spikes" title="Finding the spikes">
		<p>
			A fixed threshold does not work. Ten bits is ordinary in a list of names and remarkable in a
			paragraph of boilerplate. So Primer compares each token with the rest of its own text: how far
			it sits above the median, in units of spread. The spread is the median absolute deviation,
			scaled by 1.4826 so it reads like a standard deviation. Unlike the standard deviation, a few
			extreme tokens cannot inflate it and hide themselves.
		</p>
		<Figure
			number={2}
			title="A surprisal spike"
			caption="Invented values. The median and threshold are computed from them exactly as Primer does. The two tokens over the line form one span; tokens far below the median are not flagged, since an unusually predictable token is not what anyone is looking for."
		>
			<SpikeFigure />
		</Figure>
		<p>
			A token whose robust z-score reaches 3.5 is a spike, the cut-off Iglewicz and Hoaglin
			recommend for this score. Neighbouring tokens over the line are reported as one span. When
			more than half the tokens are equally surprising the median deviation is zero, which is
			exactly the case of one spike in flat text, so the spread falls back to the mean absolute
			deviation.
		</p>
	</Section>

	<Section id="entropy" title="Entropy and low confidence">
		<p>
			Surprisal says how unexpected the token that came was. Entropy says how unsure the model was
			before it came: the expected surprisal over every token it might have seen. A model that knows
			what comes next has low entropy whatever then arrives.
		</p>
		<Figure
			number={3}
			title="Confident and unsure"
			caption="Invented probabilities over the five likeliest tokens. The entropy is computed from them. One dominant option is under one bit; five equal ones would be log₂ 5, about 2.3."
		>
			<EntropyFigure />
		</Figure>
		<p>
			Primer flags a <em>low confidence</em> span where entropy has a robust z-score of at least 2
			for at least three tokens in a row. One hesitant token is common; a stretch of them suggests
			the model has lost track of where the text is going. Through Chat, entropy is computed from
			the top 20 options only, so it is a lower bound, and the page says so.
		</p>
	</Section>

	<Section id="outliers" title="Activation outliers">
		<p>
			Surprisal looks at what the model predicts. Outliers look at what it is thinking while it
			predicts: how far the residual stream at each token is from the residual stream of ordinary
			text, at a layer you choose. This needs Lab's own model, since it reads the inside of it.
		</p>
		<Figure
			number={4}
			title="Why the distance is Mahalanobis"
			caption="Reference tokens vary much more along some directions than others. A is close in straight-line distance but off the direction ordinary text varies along, so it is an outlier. B is further away but along that direction, so it is not."
		>
			<OutlierFigure />
		</Figure>
		<p>
			The reference is the residual stream at every token of a set of plain, neutral paragraphs
			that ship with Lab, built the first time a layer is asked for. Four of every five paragraphs
			fit a mean and a covariance. The covariance is shrunk with the Ledoit-Wolf estimator, because
			there are about as many reference tokens as dimensions and the plain sample covariance could
			not be inverted. The fifth paragraph is held out and sets the threshold at the 99th
			percentile of its distances, so ordinary text the fit never saw crosses it about one token in
			a hundred.
		</p>
	</Section>

	<Section id="probes" title="Probes">
		<p>
			A probe is a small classifier trained on the model's residual stream to recognise one thing
			you care about. You give it examples of each side, for instance passages that contain an
			injected instruction and passages that do not. Lab reads the residual stream after a chosen
			layer at the last token of each example and fits a logistic regression to separate them.
		</p>
		<Figure
			number={5}
			title="A probe on held-out examples"
			caption="Invented scores. Each dot is an example the probe was not fitted on, placed by its score. The probe's accuracy counts only if it beats always guessing the larger side by more than two standard errors."
		>
			<ProbeFigure />
		</Figure>
		<p>
			Accuracy is measured by cross-validation, with as many folds as the smaller side allows, up to
			five. Chance is the share of the larger side, and Lab reports whether the probe beats it by
			more than two standard errors. A probe that does not is reported as such, because with a few
			dozen examples in thousands of dimensions a perfect fit to the training data is easy and
			means nothing. In Anomalies a probe gives a probability at every token.
		</p>
	</Section>

	<Section id="caveats" title="What it cannot tell you">
		<ul>
			<li>
				Surprising is not the same as wrong. A rare name, a formula, or a switch of language will
				spike as readily as an injected instruction.
			</li>
			<li>
				A spike is relative to its own text. In a passage that is surprising throughout, nothing
				stands out.
			</li>
			<li>
				The outlier threshold describes the bundled neutral paragraphs. Technical writing in your
				library may sit further out than they do without anything being wrong with it.
			</li>
			<li>
				A probe recognises what its examples have in common, which may not be what you meant them
				to have in common.
			</li>
		</ul>
	</Section>

	<Section id="references" title="References">
		<References {references} />
	</Section>
</div>
