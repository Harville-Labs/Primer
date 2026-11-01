import { ApiError, asProblem } from './client';
import type {
	ActivationRequest,
	ActivationVector,
	InspectRequest,
	InspectResponse,
	LabStatus,
	ProblemDetail,
} from './types';

/**
 * Talking to Lab.
 *
 * Its own client for the reason Chat has one: a separate service with its
 * own address, which is optional, and which can be down while the rest of
 * Primer is fine.
 */
export interface LabApiOptions {
	fetch?: typeof globalThis.fetch;
	baseUrl?: string;
}

/**
 * What a user reads when Lab did not answer at all.
 *
 * Named for Lab, because the alternative - the transport error reaching a
 * catch-all - is the same "something went wrong" every unrelated bug
 * produces, and sends whoever reads it to look at Primer rather than at the
 * one optional service that is not running. Lab loads a model when it
 * starts, which takes a while, so trying again is the right advice.
 */
export const LAB_UNREACHABLE: ProblemDetail = {
	code: 'lab_unreachable',
	title: 'Lab is unreachable',
	status: 503,
	detail:
		'Lab did not answer. It may still be starting and loading its model; try again in a minute. ' +
		'If it keeps happening, the Lab service is not running.',
	request_id: null,
};

export class LabApi {
	private readonly fetch: typeof globalThis.fetch;
	private readonly baseUrl: string;

	constructor(options: LabApiOptions = {}) {
		this.fetch = options.fetch ?? globalThis.fetch;
		this.baseUrl = (options.baseUrl ?? '').replace(/\/$/, '');
	}

	private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
		let response: Response;
		try {
			response = await this.fetch(`${this.baseUrl}${path}`, init);
		} catch {
			// Only the transport failing lands here; an answer with an error
			// status is Lab's own words, read below.
			throw new ApiError(LAB_UNREACHABLE);
		}
		if (!response.ok) {
			let problem: ProblemDetail;
			try {
				problem = asProblem(await response.json(), response.status);
			} catch {
				problem = {
					code: 'unexpected_response',
					title: 'Unexpected response',
					status: response.status,
					detail: `Lab replied with ${response.status}.`,
					request_id: null,
				};
			}
			throw new ApiError(problem);
		}
		return (await response.json()) as T;
	}

	/** The resident model, any switch in progress, and how many runs are waiting. */
	status(): Promise<LabStatus> {
		return this.request('/api/v1/lab/status');
	}

	inspect(payload: InspectRequest): Promise<InspectResponse> {
		return this.post('/api/v1/lab/inspect', payload);
	}

	activation(payload: ActivationRequest): Promise<ActivationVector> {
		return this.post('/api/v1/lab/activation', payload);
	}

	private post<T>(path: string, payload: unknown): Promise<T> {
		return this.request(path, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(payload),
		});
	}
}
