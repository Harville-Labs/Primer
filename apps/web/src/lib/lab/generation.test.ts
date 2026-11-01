import { describe, expect, it } from 'vitest';
import type { GenerationCompleted, InspectedToken } from '#lib/api/types.ts';
import { reading } from './fixture';
import {
	divergence,
	editEffect,
	editTarget,
	emptyGeneration,
	foldGeneration,
	type GenerationView,
	markTokens,
} from './generation';

const t = (id: number): InspectedToken => ({ id, text: `w${id}`, special: false });

function token(id: number, run: 'baseline' | 'edited', index: number, tokenId: number) {
	return { id, type: 'token', run, index, token: t(tokenId) };
}

function completed(id: number, fields: Partial<GenerationCompleted>) {
	return {
		id,
		type: 'completed',
		model: reading().model,
		prompt: [t(1)],
		baseline: [],
		edited: [],
		diverged_at: null,
		target: null,
		...fields,
	};
}

function fold(...events: Record<string, unknown>[]): GenerationView {
	return events.reduce<GenerationView>(
		(view, event) => foldGeneration(view, event),
		emptyGeneration(),
	);
}

describe('folding the stream', () => {
	it('puts each token in the answer it belongs to', () => {
		const view = fold(
			token(0, 'baseline', 0, 5),
			token(1, 'baseline', 1, 6),
			token(2, 'edited', 0, 5),
		);

		expect(view.baseline.map((each) => each.id)).toEqual([5, 6]);
		expect(view.edited.map((each) => each.id)).toEqual([5]);
	});

	it('drops an event it has already seen, so a replay cannot repeat a token', () => {
		const view = fold(token(0, 'baseline', 0, 5), token(0, 'baseline', 0, 5));

		expect(view.baseline).toHaveLength(1);
	});

	it('says how many runs are ahead while queued, and stops once a token arrives', () => {
		const waiting = fold({ id: 0, type: 'queued', ahead: 2 });
		expect(waiting.ahead).toBe(2);

		expect(foldGeneration(waiting, token(1, 'baseline', 0, 5)).ahead).toBeNull();
	});

	it('takes the answers from the comparison once Lab has made it', () => {
		const view = fold(
			token(0, 'baseline', 0, 5),
			completed(1, { baseline: [t(5), t(6)], edited: [t(5), t(7)], diverged_at: 1 }),
		);

		expect(view.baseline.map((each) => each.id)).toEqual([5, 6]);
		expect(view.completed?.diverged_at).toBe(1);
	});

	it('keeps the tokens so far when the run fails, beside Lab’s words for why', () => {
		const view = fold(token(0, 'baseline', 0, 5), {
			id: 1,
			type: 'error',
			problem: {
				code: 'internal_error',
				title: 'The run failed',
				status: 500,
				detail: 'Try again.',
			},
		});

		expect(view.baseline).toHaveLength(1);
		expect(view.failure).toEqual({ title: 'The run failed', detail: 'Try again.' });
	});

	it('skips an event type it does not know', () => {
		const view = fold({ id: 0, type: 'heartbeat' }, token(1, 'baseline', 0, 5));

		expect(view.baseline).toHaveLength(1);
		expect(view.failure).toBeNull();
	});
});

describe('the diff', () => {
	it('marks the first divergent token in both answers, and everything after it', () => {
		const view = fold(
			completed(0, { baseline: [t(1), t(2), t(3)], edited: [t(1), t(9), t(3)], diverged_at: 1 }),
		);

		const marked = markTokens(view);

		expect(marked.baseline.map((each) => each.agreement)).toEqual(['same', 'first', 'after']);
		// The third tokens match, but they continue different texts.
		expect(marked.edited.map((each) => each.agreement)).toEqual(['same', 'first', 'after']);
	});

	it('marks nothing when the answers agree', () => {
		const view = fold(completed(0, { baseline: [t(1), t(2)], edited: [t(1), t(2)] }));

		expect(markTokens(view).edited.every((each) => each.agreement === 'same')).toBe(true);
	});

	it('while streaming, finds a difference only where both answers have reached', () => {
		// The edited answer has not reached position 1 yet: it may still match.
		const early = fold(
			token(0, 'baseline', 0, 1),
			token(1, 'baseline', 1, 2),
			token(2, 'edited', 0, 1),
		);
		expect(divergence(early)).toBeNull();

		expect(divergence(foldGeneration(early, token(3, 'edited', 1, 8)))).toBe(1);
	});

	it('follows Lab once it has answered, which counts a shorter answer as a difference', () => {
		const view = fold(completed(0, { baseline: [t(1), t(2)], edited: [t(1)], diverged_at: 1 }));

		expect(divergence(view)).toBe(1);
	});
});

describe('naming an edit', () => {
	it('says the layer, the site and the component', () => {
		expect(
			editTarget({
				site: 'mlp_neuron',
				layer: 3,
				index: 412,
				op: 'zero',
				value: 0,
				positions: 'all',
			}),
		).toBe('L3 · neuron 412');
		expect(editTarget({ site: 'attn_out', layer: 0, op: 'zero', value: 0, positions: 'all' })).toBe(
			'L0 · attn out',
		);
	});

	it('says what each operation does in a few characters', () => {
		const edit = { site: 'resid_post' as const, layer: 0, positions: 'all' as const };
		expect(editEffect({ ...edit, op: 'scale', value: 0.5 })).toBe('×0.5');
		expect(editEffect({ ...edit, op: 'clamp', value: 10 })).toBe('= 10');
		expect(editEffect({ ...edit, op: 'add', value: -1.5 })).toBe('−1.5');
		expect(editEffect({ ...edit, op: 'add', value: 2 })).toBe('+2');
		expect(editEffect({ ...edit, op: 'zero', value: 0 })).toBe('zero');
	});

	it('names a steering vector by its label', () => {
		const edit = {
			site: 'resid_post' as const,
			layer: 1,
			op: 'add' as const,
			value: 1,
			positions: 'all' as const,
		};
		expect(editTarget({ ...edit, vector_id: 'a'.repeat(32) }, 'formal')).toBe('L1 · steer formal');
	});
});
