import { parseEvents } from '#lib/api/sse.ts';
import type { StudioGeneration, StudioImage } from '#lib/api/types.ts';
import { asFailure, type Failure, problemOf } from '#lib/lab/failure.ts';
import { type Offer, offerOf } from './offer';

/**
 * The Generate tab's state: the form, the run in progress, and the history.
 *
 * Here rather than in the page so it can be asserted against: the page
 * renders what this says and calls what it offers.
 */

export interface GeneratorOptions {
	/** The browser's fetch in the page; a recording one in tests. */
	fetch?: typeof globalThis.fetch;
}

/** Said when a stream ends without saying how the run went. */
const CUT_OFF: Failure = {
	title: 'The generation stopped',
	detail:
		'The connection closed before the images arrived. Any that were made will be in your history after a reload.',
};

export class Generator {
	readonly offers: Offer[];
	offerKey = $state('');
	prompt = $state('');
	negativePrompt = $state('');
	size = $state('');
	count = $state(1);
	/** Null leaves the choice to the provider, or to Primer for a template. */
	seed = $state<number | null>(null);
	steps = $state<number | null>(null);

	running = $state(false);
	progress = $state<{ value: number; max: number } | null>(null);
	failure = $state<Failure | null>(null);
	// Raw: each list is replaced whole, never edited in place.
	results = $state.raw<StudioImage[]>([]);
	history = $state.raw<StudioImage[]>([]);
	/** False once a page of history came back empty. */
	more = $state(true);

	private readonly fetch: typeof globalThis.fetch;

	constructor(offers: Offer[], history: StudioImage[], options: GeneratorOptions = {}) {
		this.offers = offers;
		this.history = history;
		this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
		this.pick(offers[0]?.key ?? '');
	}

	get offer(): Offer | null {
		return this.offers.find((each) => each.key === this.offerKey) ?? null;
	}

	/** Choose a model or template, keeping what still fits it. */
	pick(key: string): void {
		this.offerKey = key;
		const offer = this.offer;
		if (!offer) return;
		if (!offer.sizes.includes(this.size)) this.size = offer.sizes[0] ?? '';
		this.count = Math.min(Math.max(this.count, 1), offer.maxCount);
	}

	/**
	 * What will be sent: only the settings this offer takes.
	 *
	 * A value typed for one template and left in the form when another is
	 * picked is not sent to the second, which would refuse it - the field
	 * is hidden, and a hidden setting must not still apply.
	 */
	get request(): StudioGeneration | null {
		const offer = this.offer;
		if (!offer || !this.prompt.trim()) return null;
		const negative = this.negativePrompt.trim();
		return {
			provider_id: offer.providerId,
			model: offer.model,
			operation: 'generate',
			prompt: this.prompt,
			size: this.size,
			count: Math.min(this.count, offer.maxCount),
			negative_prompt: offer.negativePrompt && negative ? negative : null,
			seed: offer.seed ? this.seed : null,
			steps: offer.steps ? this.steps : null,
		};
	}

	async run(): Promise<void> {
		const request = this.request;
		if (!request || this.running) return;
		this.running = true;
		this.failure = null;
		this.progress = null;
		try {
			const response = await this.fetch('/studio/generations', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(request),
			});
			if (!response.ok || !response.body) throw await problemOf(response);
			let settled = false;
			for await (const event of parseEvents(response.body)) {
				if (event.type === 'progress') {
					this.progress = { value: Number(event.value), max: Number(event.max) };
				} else if (event.type === 'done') {
					const images = event.images as StudioImage[];
					this.results = images;
					this.history = [...images, ...this.history];
					settled = true;
				} else if (event.type === 'failed') {
					this.failure = {
						title: String(event.title),
						detail: String(event.detail ?? event.title),
					};
					settled = true;
				}
			}
			if (!settled) this.failure = CUT_OFF;
		} catch (failure) {
			this.failure = asFailure(failure);
		} finally {
			this.running = false;
			this.progress = null;
		}
	}

	/**
	 * Fill the form from how an image was made, to make it again or vary it.
	 *
	 * False when this deployment no longer offers what made it, and the form
	 * is left as it was: half-filling it would send the old prompt to a
	 * different model, which is not making the same thing again.
	 */
	reuse(image: StudioImage): boolean {
		const offer = offerOf(this.offers, image);
		const made = image.provenance;
		if (!offer || !made) return false;
		this.pick(offer.key);
		this.prompt = made.prompt;
		this.negativePrompt = made.negative_prompt ?? '';
		if (offer.sizes.includes(made.size)) this.size = made.size;
		this.seed = made.seed ?? null;
		this.steps = made.steps ?? null;
		return true;
	}

	/** The next page back, from the oldest image already shown. */
	async older(): Promise<void> {
		const oldest = this.history.at(-1);
		if (!oldest || !this.more) return;
		try {
			const response = await this.fetch(
				`/studio/images?before=${encodeURIComponent(oldest.created_at)}`,
			);
			if (!response.ok) throw await problemOf(response);
			const page = (await response.json()) as StudioImage[];
			const shown = new Set(this.history.map((each) => each.id));
			this.history = [...this.history, ...page.filter((each) => !shown.has(each.id))];
			this.more = page.length > 0;
		} catch (failure) {
			this.failure = asFailure(failure);
		}
	}

	async remove(image: StudioImage): Promise<void> {
		try {
			const response = await this.fetch(`/studio/images/${image.id}`, { method: 'DELETE' });
			if (!response.ok) throw await problemOf(response);
			this.history = this.history.filter((each) => each.id !== image.id);
			this.results = this.results.filter((each) => each.id !== image.id);
		} catch (failure) {
			this.failure = asFailure(failure);
		}
	}
}
