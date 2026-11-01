import type { BuiltGraph, GraphEdge, GraphNode } from '#lib/api/types.ts';
import { visibleToken } from './heatmap';

/**
 * Where an attribution graph's nodes go on screen.
 *
 * Laid out the way the model computed it: one column per prompt position,
 * the prompt along the bottom, a row per layer above it, and the predicted
 * tokens along the top. A layered drawing rather than a force-directed
 * one, because the question a reader brings is "what happened at layer 12
 * over this word", and a layout that moves nodes for legibility answers
 * that with a hunt.
 */

export const COLUMN = 76;
export const ROW = 40;
const PAD = 28;
/** Room on the left for the layer labels. */
export const GUTTER = 40;
/** Features sharing a cell are fanned out by at most this much. */
const SPREAD = 11;

export interface Placed {
	node: GraphNode;
	x: number;
	y: number;
}

export interface PlacedEdge {
	edge: GraphEdge;
	from: Placed;
	to: Placed;
}

export interface Layout {
	width: number;
	height: number;
	nodes: Placed[];
	edges: PlacedEdge[];
	/** Layer rows, bottom to top, for the gutter. */
	rows: { label: string; y: number }[];
	/** The prompt's tokens under their columns. */
	columns: { token: string; x: number }[];
	/** The strongest edge's magnitude, which every edge's width is scaled to. */
	strongest: number;
}

/**
 * The part of a graph worth drawing at `influence`.
 *
 * Tokens and logits are always kept: they are the question and the answer,
 * and a graph without them cannot be read at all. Features and errors are
 * kept when their cumulative influence is within the cutoff - the same
 * measure the builder pruned by, so the slider continues its pruning rather
 * than inventing another.
 */
export function visible(graph: BuiltGraph, influence: number): BuiltGraph {
	const nodes = graph.nodes.filter(
		(node) => node.kind === 'token' || node.kind === 'logit' || node.influence <= influence,
	);
	const kept = new Set(nodes.map((node) => node.id));
	const edges = graph.edges.filter((edge) => kept.has(edge.source) && kept.has(edge.target));
	return { ...graph, nodes, edges };
}

export function layout(graph: BuiltGraph, numLayers: number): Layout {
	const positions = Math.max(graph.tokens.length, 1);
	const width = GUTTER + PAD * 2 + positions * COLUMN;
	// Tokens are row 0, layer n is row n + 1, logits are the row past the last layer.
	const rowCount = numLayers + 2;
	const height = PAD * 2 + (rowCount - 1) * ROW;
	const yOf = (row: number) => PAD + (rowCount - 1 - row) * ROW;
	const xOf = (position: number) => GUTTER + PAD + position * COLUMN + COLUMN / 2;

	const cells = new Map<string, GraphNode[]>();
	const logits: GraphNode[] = [];
	for (const node of graph.nodes) {
		if (node.kind === 'logit') {
			logits.push(node);
			continue;
		}
		const key = `${rowOf(node)}:${node.position}`;
		const cell = cells.get(key);
		if (cell) cell.push(node);
		else cells.set(key, [node]);
	}

	const placed = new Map<string, Placed>();
	for (const cell of cells.values()) {
		// Most influential in the middle of the fan, where edges cross least.
		const ordered = [...cell].sort((a, b) => a.influence - b.influence);
		const step = Math.min(SPREAD, (COLUMN - 16) / ordered.length);
		ordered.forEach((node, index) => {
			const offset = (index % 2 === 0 ? 1 : -1) * Math.ceil(index / 2) * step;
			placed.set(node.id, {
				node,
				x: xOf(node.position) + offset,
				y: yOf(rowOf(node)),
			});
		});
	}
	// Spread over the last columns rather than stacked on the last one:
	// they are all read from there, and stacked they cannot be told apart.
	const likeliest = [...logits].sort((a, b) => (b.probability ?? 0) - (a.probability ?? 0));
	const last = xOf(positions - 1);
	likeliest.forEach((node, index) => {
		placed.set(node.id, {
			node,
			x: Math.max(GUTTER + PAD, last - index * COLUMN * 0.75),
			y: yOf(rowCount - 1),
		});
	});

	const edges: PlacedEdge[] = [];
	let strongest = 0;
	for (const edge of graph.edges) {
		const from = placed.get(edge.source);
		const to = placed.get(edge.target);
		if (!from || !to) continue;
		edges.push({ edge, from, to });
		strongest = Math.max(strongest, Math.abs(edge.weight));
	}
	// Weakest first, so the strong ones are drawn over them.
	edges.sort((a, b) => Math.abs(a.edge.weight) - Math.abs(b.edge.weight));

	return {
		width,
		height,
		nodes: [...placed.values()],
		edges,
		rows: [
			{ label: 'in', y: yOf(0) },
			...Array.from({ length: numLayers }, (_, layer) => ({
				label: `L${layer}`,
				y: yOf(layer + 1),
			})),
			{ label: 'out', y: yOf(rowCount - 1) },
		],
		columns: graph.tokens.map((token, position) => ({
			token: visibleToken(token),
			x: xOf(position),
		})),
		strongest,
	};
}

function rowOf(node: GraphNode): number {
	return node.kind === 'token' ? 0 : (node.layer ?? 0) + 1;
}

/** How a node is named on screen: its feature, or the text it stands for. */
export function nodeName(node: GraphNode): string {
	switch (node.kind) {
		case 'feature':
			return `L${node.layer} #${node.feature}`;
		case 'error':
			return `L${node.layer} error`;
		default:
			return visibleToken(node.token ?? '');
	}
}

export interface Neighbour {
	node: GraphNode;
	weight: number;
}

/**
 * What a node was moved by and what it moved, strongest first.
 *
 * Read from the graph as drawn, so a neighbour hidden by the slider is not
 * offered as a reason.
 */
export function neighbours(
	graph: BuiltGraph,
	id: string,
	top = 6,
): { from: Neighbour[]; to: Neighbour[] } {
	const byId = new Map(graph.nodes.map((node) => [node.id, node]));
	const strongest = (pairs: [string, number][]) =>
		pairs
			.flatMap(([other, weight]) => {
				const node = byId.get(other);
				return node ? [{ node, weight }] : [];
			})
			.sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight))
			.slice(0, top);
	return {
		from: strongest(
			graph.edges.filter((edge) => edge.target === id).map((edge) => [edge.source, edge.weight]),
		),
		to: strongest(
			graph.edges.filter((edge) => edge.source === id).map((edge) => [edge.target, edge.weight]),
		),
	};
}
