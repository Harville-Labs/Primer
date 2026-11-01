import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DeploymentCapabilities, DocumentSummary } from '#lib/api/types.ts';
import { ChatUploads } from '#lib/chat-uploads.svelte.ts';
import type { UploadResult } from '#lib/upload-client.ts';

const capabilities: DeploymentCapabilities = {
	auth_enabled: true,
	ingestion_available: true,
	chat_available: true,
	tools_available: false,
	lab_available: false,
	is_admin: false,
	max_upload_bytes: 1024,
	supported_extensions: ['.pdf', '.md'],
};

const destination = { libraryId: '', libraryName: 'Notes', started: false, capabilities };
const file = (name: string, body = 'text') => new File([body], name);

/**
 * What Control answers with when a file lands. The document itself is left
 * empty: the controller reports where a file went, not what became of it.
 */
function landed(libraryId: string, created = false): UploadResult {
	return {
		libraryId,
		libraryName: created ? 'Notes' : null,
		created,
		document: {} as DocumentSummary,
	};
}

/** A fake upload that records which library each file was sent to. */
function recordingUpload(results: (UploadResult | Error)[]) {
	const calls: { name: string; libraryId: string | undefined }[] = [];
	const upload = async (sent: File, libraryId?: string): Promise<UploadResult> => {
		calls.push({ name: sent.name, libraryId });
		const result = results.shift();
		if (!result) throw new Error('No more results were scripted.');
		if (result instanceof Error) throw result;
		return result;
	};
	return { calls, upload };
}

function uploads(upload: ReturnType<typeof recordingUpload>['upload']) {
	const announced: string[] = [];
	const controller = new ChatUploads({
		upload,
		discard: async () => {},
		announce: (message) => announced.push(message),
		lingerMs: 1000,
	});
	return { controller, announced };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('ChatUploads', () => {
	it('sends the second file to the library the first one created', async () => {
		// Otherwise every file dropped into an empty chat starts its own
		// library, and the user is asked to keep three of them.
		const { calls, upload } = recordingUpload([landed('lib-1', true), landed('lib-1')]);
		const { controller } = uploads(upload);

		const linked = await controller.accept([file('a.md'), file('b.md')], destination);

		expect(calls.map((call) => call.libraryId)).toEqual([undefined, 'lib-1']);
		expect(linked).toBe('lib-1');
		expect(controller.offered).toEqual({ id: 'lib-1', name: 'Notes', documents: 2 });
	});

	it('uploads files into an existing library several at once', async () => {
		// One at a time, a folder of small files spends most of its wait with
		// the connection idle between them.
		let inFlight = 0;
		let peak = 0;
		const controller = new ChatUploads({
			upload: async () => {
				inFlight += 1;
				peak = Math.max(peak, inFlight);
				await Promise.resolve();
				inFlight -= 1;
				return landed('lib-1');
			},
			discard: async () => {},
			announce: () => {},
		});
		const files = ['a', 'b', 'c', 'd', 'e', 'f'].map((name) => file(`${name}.md`));

		await controller.accept(files, { ...destination, libraryId: 'lib-1' });

		expect(peak).toBeGreaterThan(1);
		expect(peak).toBeLessThanOrEqual(4);
	});

	it('lets the next file create the library when the first one fails', async () => {
		// Otherwise the rest are sent with no library while one is being
		// created for them, and each starts its own.
		const { calls, upload } = recordingUpload([
			new Error('Control is not reachable.'),
			landed('lib-1', true),
			landed('lib-1'),
		]);
		const { controller } = uploads(upload);

		const linked = await controller.accept([file('a.md'), file('b.md'), file('c.md')], destination);

		expect(calls.map((call) => call.libraryId)).toEqual([undefined, undefined, 'lib-1']);
		expect(linked).toBe('lib-1');
	});

	it('uploads the rest when one file is refused', async () => {
		const { calls, upload } = recordingUpload([landed('lib-1')]);
		const { controller } = uploads(upload);

		await controller.accept([file('photo.png'), file('a.md')], destination);

		expect(calls.map((call) => call.name)).toEqual(['a.md']);
		expect(controller.error).toContain('photo.png');
	});

	it('refuses a file for a conversation that has no library', async () => {
		// The file would land somewhere this conversation cannot be asked
		// about, and the upload would look like it worked.
		const { calls, upload } = recordingUpload([]);
		const { controller, announced } = uploads(upload);

		const linked = await controller.accept([file('a.md')], { ...destination, started: true });

		expect(calls).toHaveLength(0);
		expect(linked).toBe('');
		expect(announced).toEqual([controller.error]);
		expect(controller.error).toContain('Start a new chat');
	});

	it('keeps a fresher success message when an older one expires', async () => {
		const { upload } = recordingUpload([landed('lib-1'), landed('lib-1')]);
		const { controller } = uploads(upload);

		await controller.accept([file('a.md')], destination);
		vi.advanceTimersByTime(600);
		await controller.accept([file('b.md')], destination);
		// The first message's timer fires here; it must not clear the second.
		vi.advanceTimersByTime(600);

		expect(controller.success).toBe('b.md added to Notes.');
		vi.advanceTimersByTime(600);
		expect(controller.success).toBe('');
	});

	it('shows a finished file before it leaves', async () => {
		const { upload } = recordingUpload([landed('lib-1')]);
		const { controller } = uploads(upload);

		await controller.accept([file('a.md')], destination);

		expect(controller.pending.map((pending) => pending.status)).toEqual(['complete']);
		vi.advanceTimersByTime(1000);
		expect(controller.pending).toEqual([]);
	});

	it('drops a failed file and says why', async () => {
		const { upload } = recordingUpload([new Error('Control is not reachable.')]);
		const { controller } = uploads(upload);

		await controller.accept([file('a.md')], destination);

		expect(controller.pending).toEqual([]);
		expect(controller.error).toBe('Control is not reachable.');
	});
});
