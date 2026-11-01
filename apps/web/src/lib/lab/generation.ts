import type { RawEvent } from '#lib/api/sse.ts';
import type {
	GenerationCompleted,
	InspectedToken,
	Intervention,
	InterventionOp,
	InterventionSite,
	ProblemDetail,
} from '#lib/api/types.ts';
import type { Failure } from './failure';

/**
 * A generation with edits, as the browser sees it: two answers arriving a
 * token at a time, then the comparison Lab made of them.
 *
 * Pure, so the folding and the diff can be asserted against without a
 * stream or a component.
 */

export interface GenerationView {
	baseline: InspectedToken[];
	edited: InspectedToken[];
	/** Runs ahead of this one while it waits; null once it has started. */
	ahead: number | null;
	completed: GenerationCompleted | null;
	failure: Failure | null;
	lastEventId: number;
}

export function emptyGeneration(): GenerationView {
	return { baseline: [], edited: [], ahead: null, completed: null, failure: null, lastEventId: -1 };
}

/**
 * Fold one event into the view.
 *
 * Repeated and out-of-order events are dropped by id, as Chat's stream does,
 * and an unrecognised type is skipped rather than guessed at.
 */
export function foldGeneration(view: GenerationView, event: RawEvent): GenerationView {
	const id = typeof event.id === 'number' ? event.id : -1;
	if (id <= view.lastEventId) return view;
	const next: GenerationView = { ...view, lastEventId: id };
	switch (event.type) {
		case 'queued':
			next.ahead = Number(event.ahead);
			return next;
		case 'token': {
			next.ahead = null;
			const token = event.token as InspectedToken;
			if (event.run === 'baseline') next.baseline = [...view.baseline, token];
			else next.edited = [...view.edited, token];
			return next;
		}
		case 'completed': {
			const completed = event as unknown as GenerationCompleted;
			// Lab's lists win over what was streamed, so a dropped frame cannot
			// leave the screen disagreeing with the comparison beside it.
			return {
				...next,
				ahead: null,
				completed,
				baseline: completed.baseline,
				edited: completed.edited,
			};
		}
		case 'error': {
			// The tokens so far are kept: half an answer says how far it got.
			const problem = event.problem as ProblemDetail;
			next.ahead = null;
			next.failure = { title: problem.title, detail: problem.detail ?? problem.title };
			return next;
		}
		default:
			return next;
	}
}

/** Where a token stands against the same position in the other answer. */
export type Agreement = 'same' | 'first' | 'after';

export interface MarkedToken {
	token: InspectedToken;
	agreement: Agreement;
}

/**
 * Where the two answers first differ, by position.
 *
 * Lab's own answer once it has given one. Until then, the first position
 * both answers have reached and disagree at: a position only one of them
 * has reached yet says nothing, because the other may still write the same
 * token there.
 */
export function divergence(view: GenerationView): number | null {
	if (view.completed) return view.completed.diverged_at ?? null;
	const reached = Math.min(view.baseline.length, view.edited.length);
	for (let index = 0; index < reached; index++) {
		if (view.baseline[index].id !== view.edited[index].id) return index;
	}
	return null;
}

/**
 * Each answer's tokens, marked against the other's.
 *
 * Everything after the first difference is marked as after it, even a
 * token that happens to match: the two answers are continuing different
 * texts from there, and a match is a coincidence rather than agreement.
 */
export function markTokens(view: GenerationView): {
	baseline: MarkedToken[];
	edited: MarkedToken[];
} {
	const at = divergence(view);
	const mark = (token: InspectedToken, index: number): MarkedToken => ({
		token,
		agreement: at === null || index < at ? 'same' : index === at ? 'first' : 'after',
	});
	return { baseline: view.baseline.map(mark), edited: view.edited.map(mark) };
}

/** The value an operation starts at, chosen so a new edit visibly does something. */
export const OP_DEFAULTS: Record<InterventionOp, number> = {
	zero: 0,
	mean: 0,
	scale: 2,
	clamp: 10,
	add: 1,
};

/** The range a value's slider covers; typing into it is limited to the same. */
export const OP_RANGES: Partial<
	Record<InterventionOp, { min: number; max: number; step: number }>
> = {
	scale: { min: -4, max: 4, step: 0.05 },
	clamp: { min: -50, max: 50, step: 0.5 },
	add: { min: -20, max: 20, step: 0.1 },
};

export const OPS: { value: InterventionOp; label: string }[] = [
	{ value: 'zero', label: 'Zero' },
	{ value: 'mean', label: 'Mean' },
	{ value: 'scale', label: 'Scale' },
	{ value: 'clamp', label: 'Clamp' },
	{ value: 'add', label: 'Add' },
];

const SITE_NAMES: Record<InterventionSite, string> = {
	resid_post: 'residual',
	mlp_neuron: 'neuron',
	mlp_out: 'MLP out',
	attn_out: 'attn out',
	sae_feature: 'feature',
};

/** What an edit does, as short as the list's row has room for: `×2`, `= 10`, `+1.5`. */
export function editEffect(edit: Intervention): string {
	const value = new Intl.NumberFormat('en', { maximumFractionDigits: 2 }).format(edit.value);
	switch (edit.op) {
		case 'zero':
			return 'zero';
		case 'mean':
			return 'mean';
		case 'scale':
			return `×${value}`;
		case 'clamp':
			return `= ${value}`;
		case 'add':
			return edit.value < 0 ? `−${value.slice(1)}` : `+${value}`;
	}
}

/**
 * A short name for what an edit touches: `L3 · neuron 412`, or with a
 * label, `L3 · feature 412 · French words`. The label is a steering
 * vector's name, or a feature's, and a vector without one shows its id.
 */
export function editTarget(edit: Intervention, label?: string): string {
	const site = SITE_NAMES[edit.site];
	if (edit.vector_id) return `L${edit.layer} · steer ${label ?? edit.vector_id.slice(0, 8)}`;
	if (edit.index == null) return `L${edit.layer} · ${site}`;
	const target = `L${edit.layer} · ${site} ${edit.index}`;
	return label ? `${target} · ${label}` : target;
}
