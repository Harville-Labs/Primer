import { describe, expect, it } from 'vitest';
import type { DeploymentCapabilities } from '#lib/api/types.ts';
import {
	canScore,
	distanceShares,
	entropyBasis,
	pastThreshold,
	sourcesFrom,
	spanText,
	stripRows,
	surprisalShares,
} from './anomalies';
import { chatModel, PROBE, RESIDENT, sequence } from './fixture';

function capabilities(lab: boolean, chat: boolean): DeploymentCapabilities {
	return {
		auth_enabled: false,
		ingestion_available: true,
		chat_available: chat,
		tools_available: false,
		lab_available: lab,
		is_admin: false,
		max_upload_bytes: 1024,
		supported_extensions: ['.pdf'],
	};
}

describe('what the tab can score with', () => {
	it('is offered on a deployment with Chat and no Lab', () => {
		expect(canScore(capabilities(false, true))).toBe(true);
		expect(canScore(capabilities(true, false))).toBe(true);
		expect(canScore(capabilities(false, false))).toBe(false);
	});

	it('leaves out a model whose provider has not declared logprobs', () => {
		const sources = sourcesFrom(
			[chatModel('declared'), chatModel('silent', { supports_logprobs: false })],
			null,
		);

		expect(sources.map((source) => source.label)).toEqual(['declared · Local vLLM']);
	});

	it('scores text only where the provider declared prompt logprobs, and always on Lab', () => {
		/**
		 * The "score this text" switch is shown from this flag. An endpoint
		 * offered it without the declaration would have every attempt
		 * refused by Chat.
		 */
		const sources = sourcesFrom(
			[chatModel('answers'), chatModel('reads', { supports_prompt_logprobs: true })],
			RESIDENT,
		);

		expect(sources.map((source) => [source.kind, source.scoresText])).toEqual([
			['endpoint', false],
			['endpoint', true],
			['lab', true],
		]);
		expect(sources[2].label).toBe('qwen3-tiny · Lab');
	});

	it('keeps two providers serving the same model apart', () => {
		const sources = sourcesFrom(
			[chatModel('llama', { provider_id: 'a' }), chatModel('llama', { provider_id: 'b' })],
			null,
		);

		expect(new Set(sources.map((source) => source.key)).size).toBe(2);
	});
});

describe('naming the numbers', () => {
	it('says an endpoint entropy saw only the top k, and is a lower bound', () => {
		expect(entropyBasis(sequence([1], { top_k: 20 }))).toBe(
			'entropy over the top 20 only, a lower bound',
		);
		expect(entropyBasis(sequence([1], { estimate: 'full', top_k: null }))).toBe(
			'entropy over the whole vocabulary',
		);
	});

	it('colours each token against the most surprising one in the same text', () => {
		expect(surprisalShares(sequence([1, 4, 2]))).toEqual([0.25, 1, 0.5]);
		expect(surprisalShares(sequence([0, 0]))).toEqual([0, 0]);
	});

	it('reads a span as the text of the tokens it covers, end excluded', () => {
		const score = sequence([1, 9, 9, 1]);
		const span = { kind: 'surprisal_spike' as const, start: 1, end: 3, peak_index: 1, peak_z: 4 };

		expect(spanText(score, span)).toBe(' t1 t2');
	});
});

describe("Lab's detectors on the strip", () => {
	const outliers = { layer: 3, distances: [5, 10, 30], threshold: 10, reference_tokens: 400 };

	it('colours a distance against the threshold, which sits at half strength', () => {
		/**
		 * Against the threshold rather than the text's own peak: scaled to the
		 * peak, a text with nothing unusual in it would still have one token
		 * drawn as darkly as a true outlier.
		 */
		expect(distanceShares(outliers)).toEqual([0.25, 0.5, 1]);
		expect(distanceShares({ ...outliers, distances: [5, 6] })).toEqual([0.25, 0.3]);
	});

	it('counts only the tokens strictly past the threshold', () => {
		expect(pastThreshold(outliers)).toBe(1);
	});

	it('is the surprisal row alone when neither detector was read', () => {
		const rows = stripRows(sequence([1, 2, 4]), null, null);

		expect(rows.map((row) => row.name)).toEqual(['bits']);
		expect(rows[0].labels).toEqual(['1.0', '2.0', '4.0']);
	});

	it('adds the distance and the probe above surprisal, a probe signed about one half', () => {
		const probe = { probe: PROBE, probabilities: [0, 0.5, 0.75] };

		const rows = stripRows(sequence([1, 2, 4]), outliers, probe);

		expect(rows.map((row) => row.name)).toEqual(['bits', 'L3', 'probe']);
		expect(rows[1].labels).toEqual(['0.5', '1.0', '3.0']);
		expect(rows[2].values).toEqual([-1, 0, 0.5]);
		expect(rows[2].labels).toEqual(['0.00', '0.50', '0.75']);
		expect(rows.every((row) => row.values.length === 3)).toBe(true);
	});
});
