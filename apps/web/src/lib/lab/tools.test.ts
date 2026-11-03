import { describe, expect, it } from 'vitest';
import { capabilities } from './fixture';
import { LAB_TOOLS, labTools, runs } from './tools.ts';

describe('labTools', () => {
	it('offers every tool where Lab and Chat both run', () => {
		expect(labTools(capabilities(true, true)).map((entry) => entry.tool)).toEqual([
			'neurons',
			'anomalies',
			'scan',
		]);
	});

	it('offers only Anomalies without Lab, since it can score a chat endpoint', () => {
		/** Neurons and Scan both read Lab's own model; offering them would lead to a 404. */
		expect(labTools(capabilities(false, true)).map((entry) => entry.tool)).toEqual(['anomalies']);
	});

	it('withholds Scan without Chat, which lists the passages it reads', () => {
		expect(runs('scan', capabilities(true, false))).toBe(false);
		expect(runs('neurons', capabilities(true, false))).toBe(true);
	});
});

describe('LAB_TOOLS', () => {
	it('gives every tool an explainer under /lab/about', () => {
		/** The sidebar's "What is this?" and each tool's header link here. */
		expect(LAB_TOOLS.length).toBeGreaterThan(0);
		for (const entry of LAB_TOOLS) {
			expect(entry.about).toBe(`/lab/about/${entry.tool}`);
			expect(entry.href).toBe(`/lab/${entry.tool}`);
		}
	});
});
