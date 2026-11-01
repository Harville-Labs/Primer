import { describe, expect, it } from 'vitest';
import { builtGraph } from './fixture';
import { layout, neighbours, nodeName, ROW, visible } from './graph';

describe('what is drawn', () => {
	it('keeps the prompt and the predictions however far influence is turned down', () => {
		const shown = visible(builtGraph(), 0);
		expect(shown.nodes.map((node) => node.id).sort()).toEqual(['l1', 'l2', 't0', 't1']);
		expect(shown.edges).toEqual([]);
	});

	it('hides features past the cutoff and every edge that touched them', () => {
		/**
		 * An edge whose other end is hidden would be drawn to nowhere, and
		 * read as a cause the graph never showed.
		 */
		const shown = visible(builtGraph(), 0.5);
		expect(shown.nodes.filter((node) => node.kind === 'feature').map((node) => node.id)).toEqual([
			'f1',
		]);
		expect(shown.edges.map((edge) => `${edge.source}>${edge.target}`)).toEqual(['f1>l1', 'f1>l2']);
	});
});

describe('the layout', () => {
	it('puts the prompt at the bottom, each layer above the last, and predictions on top', () => {
		const placed = layout(builtGraph(), 2);
		const y = (id: string) => placed.nodes.find((each) => each.node.id === id)?.y ?? Number.NaN;
		expect(y('t1') - y('f0')).toBe(ROW);
		expect(y('f0') - y('f1')).toBe(ROW);
		expect(y('f1') - y('l1')).toBe(ROW);
		expect(placed.rows.map((row) => row.label)).toEqual(['in', 'L0', 'L1', 'out']);
	});

	it('fans out nodes sharing a cell so neither hides the other', () => {
		const placed = layout(builtGraph(), 2);
		const at = (id: string) => placed.nodes.find((each) => each.node.id === id);
		expect(at('f1')?.y).toBe(at('e1')?.y);
		expect(at('f1')?.x).not.toBe(at('e1')?.x);
	});

	it('draws the strongest edges last, over the weak ones', () => {
		const weights = layout(builtGraph(), 2).edges.map((each) => Math.abs(each.edge.weight));
		expect(weights.length).toBeGreaterThan(0);
		expect(weights).toEqual([...weights].sort((a, b) => a - b));
	});

	it('shows the prompt with its whitespace made visible, as the heatmap does', () => {
		expect(layout(builtGraph(), 2).columns[1]?.token).not.toBe(' Paris');
	});
});

describe('reading a node', () => {
	it('names a feature by its layer and index, and a token by its text', () => {
		const [token, , feature, , error] = builtGraph().nodes;
		expect(nodeName(feature)).toBe('L0 #7');
		expect(nodeName(error)).toBe('L1 error');
		expect(nodeName(token)).toBe('<|endoftext|>');
	});

	it('lists what moved a prediction strongest first, inhibitors included', () => {
		const { from, to } = neighbours(builtGraph(), 'l1');
		expect(from.map((each) => [each.node.id, each.weight])).toEqual([
			['f1', 3],
			['e1', -0.5],
		]);
		expect(to).toEqual([]);
	});
});
