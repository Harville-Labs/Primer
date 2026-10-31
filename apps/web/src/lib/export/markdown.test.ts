import { describe, expect, it } from 'vitest';
import type { MessageSummary } from '#lib/api/types.ts';
import { copyResponseText, exportResponseMarkdown } from './markdown';

function message(overrides: Partial<MessageSummary> = {}): MessageSummary {
	return {
		id: 'message-1',
		conversation_id: 'conversation-1',
		role: 'assistant',
		state: 'completed',
		content: 'Grounded answer [1].',
		citations: [
			{
				document_id: 'doc-1',
				document_version_id: 'ver-1',
				chunk_id: 'chunk-1',
				locator: { page: 3, section: 'Findings' },
				excerpt: 'the quoted passage',
			},
		],
		error_code: null,
		created_at: '2026-08-31T00:00:00Z',
		...overrides,
	};
}

describe('markdown export', () => {
	it('exports response text and citation references', () => {
		const markdown = exportResponseMarkdown(message(), { 'ver-1': 'paper.pdf' });

		expect(markdown).toContain('Grounded answer');
		expect(markdown).toContain('[1] paper.pdf, p. 3');
		expect(markdown).not.toContain('storage://');
	});

	it('never exports internal identifiers', () => {
		// An export travels outside Primer. A chunk id means nothing to a
		// reader, and a storage path would leak how the deployment is laid out.
		const markdown = exportResponseMarkdown(message(), { 'ver-1': 'paper.pdf' });

		expect(markdown).not.toContain('chunk-1');
		expect(markdown).not.toContain('ver-1');
		expect(markdown).not.toContain('doc-1');
	});

	it('includes the section when there is one', () => {
		const markdown = exportResponseMarkdown(message(), { 'ver-1': 'paper.pdf' });
		expect(markdown).toContain('Findings');
	});

	it('copes with a citation that has no page', () => {
		// Markdown and text documents have headings rather than pages.
		const withoutPage = message({
			citations: [
				{
					document_id: 'doc-1',
					document_version_id: 'ver-1',
					chunk_id: 'chunk-1',
					locator: { page: null, section: 'Introduction' },
					excerpt: null,
				},
			],
		});

		expect(exportResponseMarkdown(withoutPage, { 'ver-1': 'notes.md' })).toContain(
			'[1] notes.md, Introduction',
		);
	});

	it('omits the sources section when nothing was cited', () => {
		const uncited = message({ citations: [], content: 'I could not find anything.' });
		expect(exportResponseMarkdown(uncited)).not.toContain('## Sources');
	});

	it('marks an answer that was cut short', () => {
		// Pasted elsewhere, a truncated answer looks complete without this.
		const failed = message({ state: 'failed', content: 'Partial answer' });
		expect(exportResponseMarkdown(failed)).toContain('cut short');
	});

	it('copies plain text with nothing added', () => {
		expect(copyResponseText(message())).toBe('Grounded answer [1].');
	});
});
