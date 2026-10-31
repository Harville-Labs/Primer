import type { DeploymentCapabilities } from './api/types';

/**
 * Deciding whether a file is worth sending.
 *
 * This is a courtesy, not a control. The server checks the bytes and rejects
 * a file whose contents disagree with its name; this only saves the user
 * from uploading a hundred megabytes to be told no. Any rule here that the
 * server does not also enforce would be a rule that does not exist.
 */

export interface Rejection {
	code: 'unsupported_extension' | 'too_large' | 'empty';
	message: string;
}

export function describeAccepted(capabilities: DeploymentCapabilities): string {
	const names: Record<string, string> = {
		'.pdf': 'PDF',
		'.docx': 'DOCX',
		'.pptx': 'PPTX',
		'.md': 'Markdown',
		'.markdown': 'Markdown',
		'.txt': 'text',
	};
	const labels = capabilities.supported_extensions.map((ext) => names[ext] ?? ext);
	return [...new Set(labels)].join(', ');
}

export function formatBytes(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	const units = ['KB', 'MB', 'GB'];
	let value = bytes / 1024;
	let unit = 0;
	while (value >= 1024 && unit < units.length - 1) {
		value /= 1024;
		unit += 1;
	}
	return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

export function rejectionFor(file: File, capabilities: DeploymentCapabilities): Rejection | null {
	const dot = file.name.lastIndexOf('.');
	const extension = dot === -1 ? '' : file.name.slice(dot).toLowerCase();

	if (!capabilities.supported_extensions.includes(extension)) {
		return {
			code: 'unsupported_extension',
			message: `${file.name} is not a supported format. Primer accepts ${describeAccepted(capabilities)} files.`,
		};
	}
	if (file.size === 0) {
		return { code: 'empty', message: `${file.name} is empty.` };
	}
	if (file.size > capabilities.max_upload_bytes) {
		return {
			code: 'too_large',
			message: `${file.name} is ${formatBytes(file.size)}, over the ${formatBytes(capabilities.max_upload_bytes)} limit.`,
		};
	}
	return null;
}

/** The longest a library name may be, matching what Control accepts. */
const MAX_LIBRARY_NAME = 120;

/**
 * A library name for a file dropped into a conversation with nowhere to put
 * it.
 *
 * Derived from the filename rather than generic, because the user is about to
 * be asked whether to keep this library and "Untitled" tells them nothing
 * about what is in it. It is a starting point, not a decision: the name is
 * theirs to change.
 */
export function libraryNameFor(filename: string): string {
	const dot = filename.lastIndexOf('.');
	const stem = dot > 0 ? filename.slice(0, dot) : filename;

	const words = stem
		// Separators people actually use in filenames, including the runs of
		// them left behind by tools that already substituted one for another.
		.replace(/[_\-.]+/g, ' ')
		// `ReportFinal` and `HTTPServer` both read badly as one word.
		.replace(/([a-z0-9])([A-Z])/g, '$1 $2')
		.replace(/\s+/g, ' ')
		.trim();

	if (!words) {
		// A file named only with separators, or `.gitignore`. Rare, and still
		// needs a name that Control will accept.
		return 'New library';
	}

	const name = words.charAt(0).toUpperCase() + words.slice(1);
	return name.length > MAX_LIBRARY_NAME ? name.slice(0, MAX_LIBRARY_NAME).trimEnd() : name;
}

/**
 * Carries a file's name alongside its upload, for naming a library after it.
 *
 * The upload body is streamed to Control without being read, so the name in
 * its multipart header is out of reach of the server that has to create the
 * library first. A header rather than a query parameter, because a URL ends
 * up in access logs and a filename can say a good deal about its contents.
 */
export const UPLOAD_FILENAME_HEADER = 'x-upload-filename';

/** Header values are Latin-1; a filename is anything. */
export function encodeFilename(name: string): string {
	return encodeURIComponent(name);
}

/** The name `encodeFilename` sent, or null if there is none worth reading. */
export function decodeFilename(header: string | null): string | null {
	if (!header) return null;
	try {
		return decodeURIComponent(header) || null;
	} catch {
		// Malformed percent-encoding came from something other than this app's
		// own client; there is no name in it to recover.
		return null;
	}
}
