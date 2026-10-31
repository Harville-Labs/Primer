import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { DeploymentCapabilities } from '#lib/api/types.ts';
import UploadDropzone from './UploadDropzone.svelte';

const capabilities: DeploymentCapabilities = {
	auth_enabled: true,
	ingestion_available: true,
	chat_available: true,
	tools_available: false,
	is_admin: false,
	max_upload_bytes: 1024,
	supported_extensions: ['.pdf', '.docx', '.pptx', '.md', '.txt'],
};

function fileInput(): HTMLInputElement {
	return screen.getByTestId('file-input') as HTMLInputElement;
}

/**
 * Upload without the browser's `accept` filter.
 *
 * `accept` is a hint to the file picker, not a control: a user can switch it
 * to "All files", or drag a file straight onto the drop zone. Letting
 * userEvent honour it would mean these tests only ever exercised files the
 * picker already allowed, which is precisely not the case being tested.
 */
async function choose(...files: File[]) {
	await userEvent.upload(fileInput(), files, { applyAccept: false });
}

describe('UploadDropzone', () => {
	it('announces rejected file types', async () => {
		render(UploadDropzone, { capabilities });

		await choose(new File(['x'], 'malware.exe'));

		expect(screen.getByRole('alert')).toHaveTextContent('not a supported format');
		expect(screen.getByRole('alert')).toHaveTextContent('PDF, DOCX, PPTX, Markdown, text');
	});

	it('does not hand a rejected file to the caller', async () => {
		// The point of the check: nothing is uploaded, not merely warned about.
		const onupload = vi.fn();
		render(UploadDropzone, { capabilities, onupload });

		await choose(new File(['x'], 'malware.exe'));

		expect(onupload).not.toHaveBeenCalled();
	});

	it('accepts a supported file', async () => {
		const onupload = vi.fn();
		render(UploadDropzone, { capabilities, onupload });

		await choose(new File(['evidence'], 'paper.pdf'));

		expect(onupload).toHaveBeenCalledTimes(1);
		expect(onupload.mock.calls[0][0][0].name).toBe('paper.pdf');
	});

	it('reports the size limit in units a person reads', async () => {
		render(UploadDropzone, { capabilities });

		await choose(new File(['x'.repeat(2048)], 'huge.pdf'));

		expect(screen.getByRole('alert')).toHaveTextContent('2.0 KB');
		expect(screen.getByRole('alert')).toHaveTextContent('1.0 KB limit');
	});

	it('reports every rejected file, not just the first', async () => {
		// Dropping five and having two silently ignored looks like a
		// half-working upload.
		render(UploadDropzone, { capabilities });

		await choose(new File(['x'], 'one.exe'), new File(['x'], 'two.zip'));

		const alert = screen.getByRole('alert');
		expect(alert).toHaveTextContent('one.exe');
		expect(alert).toHaveTextContent('two.zip');
	});

	it('passes the good files through when only some are rejected', async () => {
		const onupload = vi.fn();
		render(UploadDropzone, { capabilities, onupload });

		await choose(new File(['x'], 'good.pdf'), new File(['x'], 'bad.exe'));

		expect(onupload.mock.calls[0][0].map((f: File) => f.name)).toEqual(['good.pdf']);
		expect(screen.getByRole('alert')).toHaveTextContent('bad.exe');
	});

	it('can be operated without a pointer', async () => {
		// Dragging has no keyboard equivalent, so the same control has to
		// open a file picker on Enter.
		render(UploadDropzone, { capabilities });

		const target = screen.getByRole('button', { name: /add documents/i });
		const clicked = vi.fn();
		fileInput().addEventListener('click', clicked);

		target.focus();
		expect(target).toHaveFocus();
		await userEvent.keyboard('{Enter}');

		expect(clicked).toHaveBeenCalled();
	});

	it('announces rejections as an assertive live region', async () => {
		// role="alert" is an assertive live region by definition, so a user
		// who dropped a file elsewhere on the page hears why it was refused.
		render(UploadDropzone, { capabilities });
		expect(screen.queryByRole('alert')).toBeNull();

		await choose(new File(['x'], 'nope.exe'));

		expect(screen.getByRole('alert')).toBeInTheDocument();
	});
});
