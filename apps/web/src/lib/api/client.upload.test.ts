import { describe, expect, it } from 'vitest';
import { PrimerApi } from './client';

/**
 * The web server's half of an upload.
 *
 * The body has to reach Control as the same stream the browser sent, never
 * read into memory on the way: a server that buffers uploads needs as much
 * memory as the largest file anyone sends, times however many are in flight.
 */
describe('PrimerApi.upload', () => {
	function recording() {
		const sent: Request[] = [];
		const api = new PrimerApi({
			baseUrl: 'http://control.test',
			fetch: async (input, init) => {
				// SvelteKit's server fetch builds a Request from what it is given,
				// so a body that a Request refuses fails here the way it would there.
				sent.push(new Request(input, init));
				return Response.json({ id: 'doc-1' });
			},
		});
		return { api, sent };
	}

	it('passes the browser body on as a stream, with its multipart boundary', async () => {
		const { api, sent } = recording();
		const contentType = 'multipart/form-data; boundary=----primer';
		const body = new ReadableStream<Uint8Array>({
			start(controller) {
				controller.enqueue(new TextEncoder().encode('------primer\r\n...'));
				controller.close();
			},
		});

		await api.upload('lib-1', body, contentType);

		expect(sent).toHaveLength(1);
		expect(sent[0].url).toBe('http://control.test/api/v1/libraries/lib-1/documents');
		expect(sent[0].headers.get('content-type')).toBe(contentType);
		expect(await sent[0].text()).toBe('------primer\r\n...');
	});
});
