import { describe, expect, it } from 'vitest';
import type { DeploymentCapabilities } from '#lib/api/types.ts';
import { canScore, entropyBasis, sourcesFrom, spanText, surprisalShares } from './anomalies';
import { chatModel, RESIDENT, sequence } from './fixture';

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
