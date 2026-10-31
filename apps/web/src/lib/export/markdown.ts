import type { Citation, MessageSummary } from '#lib/api/types.ts';

/**
 * Turning an answer into something a person can paste elsewhere.
 *
 * Citations are rendered as human references - filename, page, section -
 * and never as storage keys or internal identifiers. An exported answer
 * travels outside Primer, and a chunk id means nothing to a reader while a
 * storage path would leak how the deployment is laid out.
 */

export function describeCitation(citation: Citation, filename?: string): string {
	const parts: string[] = [filename ?? 'Source'];
	if (citation.locator?.page != null) parts.push(`p. ${citation.locator.page}`);
	if (citation.locator?.section) parts.push(citation.locator.section);
	return parts.join(', ');
}

/** Plain text: the answer as written, with nothing added. */
export function copyResponseText(message: MessageSummary): string {
	return message.content;
}

/** Markdown: the answer, then its sources, in the order they were cited. */
export function exportResponseMarkdown(
	message: MessageSummary,
	filenames: Record<string, string> = {},
): string {
	const lines = [message.content.trim()];

	if (message.citations.length > 0) {
		lines.push('', '## Sources', '');
		message.citations.forEach((citation, index) => {
			const filename = filenames[citation.document_version_id] ?? filenames[citation.document_id];
			lines.push(`[${index + 1}] ${describeCitation(citation, filename)}`);
		});
	}

	if (message.state === 'failed') {
		// Marked, because an answer that stops mid-thought looks complete
		// once it is pasted somewhere without the interface around it.
		lines.push('', '_This answer was cut short before it finished._');
	}

	return lines.join('\n');
}
