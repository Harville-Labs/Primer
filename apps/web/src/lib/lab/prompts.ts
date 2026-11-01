/**
 * One prompt a line, so a list pasted from anywhere reads as one.
 *
 * Shared by everything made from two sets of prompts - steering vectors
 * and probes - so the two read a pasted list the same way.
 */
export function promptLines(text: string): string[] {
	return text
		.split('\n')
		.map((line) => line.trim())
		.filter(Boolean);
}
