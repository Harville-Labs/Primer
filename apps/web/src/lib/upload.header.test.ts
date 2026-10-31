import { describe, expect, it } from 'vitest';
import { decodeFilename, encodeFilename } from './upload';

/**
 * A filename's trip through a request header.
 *
 * HTTP headers carry Latin-1, and a name in any other script would arrive
 * mangled or be refused by `fetch` outright - which would fail the upload,
 * not just the library's name.
 */
describe('filename header', () => {
	it('survives a name in any script', () => {
		for (const name of ['report.pdf', 'Überblick 2026.md', '論文 (final).pdf', 'a%20b.txt']) {
			const header = encodeFilename(name);
			expect(() => new Headers({ 'x-upload-filename': header })).not.toThrow();
			expect(decodeFilename(header)).toBe(name);
		}
	});

	it('reads nothing from a missing or malformed header', () => {
		expect(decodeFilename(null)).toBeNull();
		expect(decodeFilename('')).toBeNull();
		expect(decodeFilename('%E0%A4%A')).toBeNull();
	});
});
