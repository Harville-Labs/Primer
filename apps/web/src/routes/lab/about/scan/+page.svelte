<script lang="ts">
	import AboutIntro from '#lib/components/lab/about/AboutIntro.svelte';
	import Figure from '#lib/components/lab/about/Figure.svelte';
	import References, { type Reference } from '#lib/components/lab/about/References.svelte';
	import MeanPeakFigure from '#lib/components/lab/about/scan/MeanPeakFigure.svelte';
	import PipelineFigure from '#lib/components/lab/about/scan/PipelineFigure.svelte';
	import RankingFigure from '#lib/components/lab/about/scan/RankingFigure.svelte';
	import Section from '#lib/components/lab/about/Section.svelte';
	import { BATCH, KEPT, LISTING } from '#lib/lab/scan.ts';

	const contents = [
		{ id: 'why', title: 'Why scan a library' },
		{ id: 'how', title: 'How a scan runs' },
		{ id: 'ranking', title: 'How passages are ranked' },
		{ id: 'reading', title: 'Reading the findings' },
		{ id: 'caveats', title: 'What it cannot tell you' },
		{ id: 'references', title: 'References' },
	];

	const references: Reference[] = [
		{
			authors: 'Greshake et al.',
			year: 2023,
			title: 'Not what you’ve signed up for: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection',
			href: 'https://arxiv.org/abs/2302.12173',
			note: 'How text in a retrieved document can steer the model that reads it.',
		},
		{
			authors: 'Alon and Kamfonas',
			year: 2023,
			title: 'Detecting Language Model Attacks with Perplexity',
			href: 'https://arxiv.org/abs/2308.14132',
			note: 'Perplexity as a filter for adversarial text, and where it falls short.',
		},
		{
			authors: 'Shannon',
			year: 1948,
			title: 'A Mathematical Theory of Communication',
			note: 'Surprisal and entropy in bits.',
		},
	];
</script>

<svelte:head>
	<title>How Scan works · Lab · Primer</title>
</svelte:head>

<div class="flex flex-col gap-12">
	<AboutIntro tool="scan" {contents}>
		Scan reads every passage in one of your libraries through Lab's model and ranks them by how
		unusual the model found them. It is Anomalies applied to a whole library at once, for finding
		the few passages worth a closer look before Primer quotes them in an answer.
	</AboutIntro>

	<Section id="why" title="Why scan a library">
		<p>
			Primer answers questions from passages it retrieves out of your libraries, and the model
			reads those passages as part of its prompt. A document can contain text written for that
			model rather than for you: an instruction to ignore the question, to leave something out,
			or to say something specific. That is called indirect prompt injection. It is hard to spot
			by reading, because it can be one sentence on page forty.
		</p>
		<p>
			Such text tends to read oddly to a language model. It changes register, addresses the reader
			directly, and does not follow from the sentences around it. Scan uses that to put the
			passages that read oddest at the top of a list, so a person can read those first.
		</p>
	</Section>

	<Section id="how" title="How a scan runs">
		<p>
			The web app asks Chat for the library's passages, the same chunks retrieval searches, up to
			{LISTING.toLocaleString('en')}. Control checks that you can read the library before any are
			listed, so a scan can only see what you could already open. The web app then sends them to
			Lab {BATCH} at a time. Each batch waits its turn in Lab's run queue like any other request,
			so a scan never holds the model while someone else is waiting for it.
		</p>
		<Figure
			number={1}
			title="From library to findings"
			caption="Lab is given text, not a library, and keeps nothing between batches. The scan lives in the page, so leaving the page or pressing Stop ends it."
		>
			<PipelineFigure />
		</Figure>
		<p>
			Each passage is scored as plain text, without a chat template, exactly as Anomalies scores
			pasted text. A passage longer than Lab's prompt limit is scored from its start and marked
			truncated, rather than refused, so one long passage does not stop the rest of the library.
		</p>
	</Section>

	<Section id="ranking" title="How passages are ranked">
		<p>
			Lab returns three numbers for each passage. <strong>Mean</strong> is the average surprisal
			per token, in bits. <strong>Peak</strong> is the surprisal of its single most surprising
			token. <strong>Probe</strong>, if you chose one, is the probability your probe gives at the
			passage's last token.
		</p>
		<Figure
			number={2}
			title="One passage that does not belong"
			caption="Invented values for a library of forty passages and one injected instruction. Ranked by mean surprisal, the injected passage is first."
		>
			<RankingFigure />
		</Figure>
		<p>
			Passages are ranked by the probe when you chose one, because a probe was trained for exactly
			the question you are asking, and by mean surprisal otherwise. A passage that could not be
			scored at all sorts last, rather than as a zero that would look like a clean bill of health.
			The {KEPT} highest are kept on screen and in a saved scan, with their text, so a saved scan still
			makes sense after the library changes.
		</p>
	</Section>

	<Section id="reading" title="Reading the findings">
		<p>
			Mean and peak answer different questions, and a finding is worth reading differently
			depending on which of them is high.
		</p>
		<Figure
			number={3}
			title="Mean and peak"
			caption="Invented values. Dense notation surprises the model a little at every token, which raises the mean. One out-of-place line raises the peak and barely moves the mean. Both are shown so a reader can tell which kind a finding is."
		>
			<MeanPeakFigure />
		</Figure>
		<p>
			A high mean usually means the passage is unlike what the model learned from: tables,
			formulas, another language, text broken by extraction from a PDF. A high peak with an
			ordinary mean means one place in an otherwise ordinary passage stood out. Each finding links
			to its document, at its page where it has one; paste the passage into Anomalies to see which
			tokens stood out and whether they form a span.
		</p>
	</Section>

	<Section id="caveats" title="What it cannot tell you">
		<ul>
			<li>
				A ranking is not a verdict. The top of the list is where to start reading, and in most
				libraries it will be tables and references rather than anything hostile.
			</li>
			<li>
				Injected text written to read naturally will not stand out by surprisal. A probe trained
				on examples of the kind of text you are worried about is the better tool for that.
			</li>
			<li>
				Scan sees passages as retrieval chunked them. An instruction split across two chunks is
				scored as two halves.
			</li>
			<li>
				Only the first {KEPT} findings are kept, and a library larger than
				{LISTING.toLocaleString('en')} passages is scanned only up to that many.
			</li>
		</ul>
	</Section>

	<Section id="references" title="References">
		<References {references} />
	</Section>
</div>
