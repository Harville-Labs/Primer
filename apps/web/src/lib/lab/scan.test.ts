import { describe, expect, it } from 'vitest';
import type { ScanFinding } from '#lib/api/types.ts';
import { passage } from './fixture';
import { batches, passageLink, rank } from './scan';

function finding(id: string, mean: number | null, probe: number | null = null): ScanFinding {
	return {
		passage: passage(id),
		scored: {
			id,
			tokens: mean === null ? 0 : 4,
			truncated: false,
			mean_bits: mean,
			peak_bits: mean,
			probe,
		},
	};
}

const ids = (findings: ScanFinding[]) => findings.map((each) => each.passage.chunk_id);

describe('ranking a scan', () => {
	it('puts the most surprising passage first without a probe', () => {
		expect(ids(rank([finding('a', 2), finding('b', 9), finding('c', 5)], false))).toEqual([
			'b',
			'c',
			'a',
		]);
	});

	it('ranks by the probe when one was read, whatever the surprisal says', () => {
		const findings = [finding('calm', 1, 0.97), finding('odd', 12, 0.02)];
		expect(ids(rank(findings, true))).toEqual(['calm', 'odd']);
	});

	/** A missing reading sorted as zero would read as a passage found clean. */
	it('puts passages with no reading last, not as a zero', () => {
		expect(ids(rank([finding('none', null), finding('zero', 0)], false))).toEqual(['zero', 'none']);
	});
});

describe('cutting a library into batches', () => {
	it('keeps every item, in order, in batches no larger than asked', () => {
		const cut = batches([1, 2, 3, 4, 5], 2);
		expect(cut).toEqual([[1, 2], [3, 4], [5]]);
	});

	it('cuts nothing into no batches', () => {
		expect(batches([], 16)).toEqual([]);
	});
});

describe('linking a finding', () => {
	it('opens the document at the passage’s page', () => {
		expect(passageLink('lib', passage('a', 7))).toBe('/libraries/lib/documents/doc/content#page=7');
	});

	it('opens the document at its start when the passage has no page', () => {
		expect(passageLink('lib', passage('a', null))).toBe('/libraries/lib/documents/doc/content');
	});
});
