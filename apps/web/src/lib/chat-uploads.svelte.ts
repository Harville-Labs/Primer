import type { DeploymentCapabilities } from '#lib/api/types.ts';
import { rejectionFor } from '#lib/upload.ts';
import type { UploadResult } from '#lib/upload-client.ts';

/** A file in flight, or one that just finished and is about to leave. */
export interface PendingUpload {
	id: string;
	file: File;
	status: 'uploading' | 'complete';
}

/** A library that dropping a file created, which the user is asked to keep. */
export interface OfferedLibrary {
	id: string;
	name: string;
	documents: number;
}

/** Where the files go, as the page knows it when they are dropped. */
export interface Destination {
	libraryId: string;
	libraryName: string | undefined;
	/** A conversation has opened, so its library can no longer change. */
	started: boolean;
	capabilities: DeploymentCapabilities;
}

interface Dependencies {
	upload: (file: File, libraryId?: string) => Promise<UploadResult>;
	discard: (libraryId: string) => Promise<void>;
	/** Says something to a screen reader; the page owns the one live region. */
	announce: (message: string) => void;
	/** How long a finished card and a success message stay on screen. */
	lingerMs?: number;
}

/**
 * Files dropped into a conversation, and what the page says about them.
 *
 * Held outside the page so the sequencing can be tested: a later upload must
 * not blank the message a fresher one set, a second file must land in the
 * library the first one created, and a refused file must not stop the rest.
 */
export class ChatUploads {
	//: One card per file in flight or just finished. A finished one lingers
	//: rather than vanishing the instant the request resolves, so the card is
	//: seen turning into its finished state instead of just disappearing.
	pending = $state<PendingUpload[]>([]);
	error = $state('');
	// Shown for a few seconds after a successful upload, because a small file
	// uploads fast enough that the card it briefly shows would be the only
	// sign anything happened - and then it is gone, and the upload looks like
	// it silently failed.
	success = $state('');
	//: Set only when dropping a file created the library holding it, which is
	//: the one case where the user should be asked whether to keep it.
	offered = $state<OfferedLibrary | null>(null);

	#successToken = 0;
	#deps: Required<Dependencies>;

	constructor(deps: Dependencies) {
		this.#deps = { lingerMs: 4000, ...deps };
	}

	/**
	 * Upload each file in turn and return the library they went into.
	 *
	 * Returns the destination's library unchanged when nothing landed. The
	 * caller links what comes back: a file dropped into a conversation was
	 * dropped there to be asked about.
	 */
	async accept(files: File[], destination: Destination): Promise<string> {
		this.error = '';
		this.success = '';
		let libraryId = destination.libraryId;
		// A file dropped here would land in a library this conversation
		// cannot be asked about, and the upload would look like it worked.
		if (destination.started && !libraryId) {
			this.#fail(
				'This conversation was started without a library. Start a new chat to ask about a file.',
			);
			return libraryId;
		}
		for (const file of files) {
			const rejection = rejectionFor(file, destination.capabilities);
			if (rejection) {
				this.#fail(rejection.message);
				continue;
			}

			const id = crypto.randomUUID();
			this.pending = [...this.pending, { id, file, status: 'uploading' }];
			this.#deps.announce(`Uploading ${file.name}.`);
			try {
				const result = await this.#deps.upload(file, libraryId || undefined);
				libraryId = result.libraryId;
				const offered = this.offered;
				if (result.created && result.libraryName) {
					this.offered = { id: result.libraryId, name: result.libraryName, documents: 1 };
				} else if (offered && offered.id === result.libraryId) {
					this.offered = { ...offered, documents: offered.documents + 1 };
				}
				const named = result.libraryName ?? destination.libraryName ?? 'the library';
				this.#succeed(`${file.name} added to ${named}.`);
				this.pending = this.pending.map((upload) =>
					upload.id === id ? { ...upload, status: 'complete' } : upload,
				);
				setTimeout(() => {
					this.pending = this.pending.filter((upload) => upload.id !== id);
				}, this.#deps.lingerMs);
			} catch (error) {
				this.#fail(error instanceof Error ? error.message : `${file.name} could not be uploaded.`);
				this.pending = this.pending.filter((upload) => upload.id !== id);
			}
		}
		return libraryId;
	}

	/** Keep the library a drop created, and stop asking. */
	keep() {
		this.offered = null;
	}

	/** Remove the library a drop created. The caller unlinks it. */
	async discard() {
		const library = this.offered;
		if (!library) return;
		this.offered = null;
		try {
			await this.#deps.discard(library.id);
		} catch (error) {
			this.error = error instanceof Error ? error.message : 'That library could not be removed.';
		}
	}

	/** Forget everything about the thread that was on screen. */
	clear() {
		this.error = '';
		this.success = '';
		this.pending = [];
		this.offered = null;
	}

	#fail(message: string) {
		this.error = message;
		this.#deps.announce(message);
	}

	// Guarded by a token so an upload that finishes later than a fresher one
	// cannot blank out the message the fresher one just set.
	#succeed(message: string) {
		this.#deps.announce(message);
		this.success = message;
		const token = ++this.#successToken;
		setTimeout(() => {
			if (this.#successToken === token) this.success = '';
		}, this.#deps.lingerMs);
	}
}
