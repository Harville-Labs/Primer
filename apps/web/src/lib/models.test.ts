import { describe, expect, it } from 'vitest';
import type { ChatModel } from '#lib/api/types.ts';
import { modelOf, qualify, unqualify } from './models';

/**
 * Naming a model so a question reaches the right machine.
 *
 * A deployment can hold several providers, and model names are not unique
 * across them - two endpoints serving `llama3.1:8b` is ordinary. If the
 * picker's value were a bare model name, choosing the local one and the
 * hosted one would be indistinguishable, and questions meant for a private
 * machine could be answered by a paid account instead.
 */

function model(id: string, provider_id?: string): ChatModel {
	return {
		id,
		default: false,
		provider_id: provider_id ?? null,
		provider_name: null,
		supports_logprobs: false,
		supports_prompt_logprobs: false,
	};
}

describe('qualifying a model', () => {
	it('names the provider alongside the model', () => {
		expect(qualify(model('llama3.1:8b', 'abc-123'))).toBe('abc-123:llama3.1:8b');
	});

	it('tells two providers serving the same model apart', () => {
		// The whole reason the pair is the name.
		expect(qualify(model('llama3.1:8b', 'workstation'))).not.toBe(
			qualify(model('llama3.1:8b', 'server')),
		);
	});

	it('leaves a model with no provider as its bare name', () => {
		expect(qualify(model('gpt-4o-mini'))).toBe('gpt-4o-mini');
	});

	it('is empty for no model, so nothing is sent', () => {
		expect(qualify(undefined)).toBe('');
	});
});

describe('splitting it back apart', () => {
	it('recovers both halves', () => {
		expect(unqualify('abc-123:llama3.1:8b')).toEqual({
			provider_id: 'abc-123',
			model: 'llama3.1:8b',
		});
	});

	it('splits on the first separator only', () => {
		// Model names contain colons - `llama3.1:8b` is the common case - so
		// splitting on the last one, or on all of them, loses the tag.
		expect(unqualify('p:qwen2.5:3b-instruct').model).toBe('qwen2.5:3b-instruct');
	});

	it('round-trips whatever was qualified', () => {
		const chosen = model('qwen2.5:3b-instruct', 'deadbeef');
		expect(unqualify(qualify(chosen))).toEqual({
			provider_id: 'deadbeef',
			model: 'qwen2.5:3b-instruct',
		});
	});

	it('treats a bare name as a model with no provider', () => {
		// A deployment with only its own provider never qualifies anything.
		expect(unqualify('gpt-4o-mini')).toEqual({ model: 'gpt-4o-mini' });
	});

	it('sends nothing at all for an empty choice', () => {
		// Which is what asks for the deployment's default.
		expect(unqualify('')).toEqual({});
	});
});

describe('modelOf', () => {
	const answer = (provider_model: string | null, provider_id: string | null = null) => ({
		role: 'assistant',
		provider_model,
		provider_id,
	});
	const question = { role: 'user', provider_model: null, provider_id: null };

	it('reopens a conversation on the model that answered it', () => {
		// Every question carries the picker's value, so a thread reopened on
		// the deployment's default sends its next question somewhere other
		// than the rest of the conversation went.
		expect(modelOf([question, answer('qwen3:8b', 'deadbeef')])).toBe('deadbeef:qwen3:8b');
	});

	it('takes the model in force, not the one it started on', () => {
		// Someone who switched halfway continues from where they switched to.
		expect(
			modelOf([question, answer('qwen3:8b', 'deadbeef'), question, answer('llama3.1:70b', 'cafe')]),
		).toBe('cafe:llama3.1:70b');
	});

	it('looks past a turn that has not been answered', () => {
		expect(modelOf([question, answer('qwen3:8b', 'deadbeef'), question])).toBe('deadbeef:qwen3:8b');
	});

	it('leaves the picker alone when nothing was recorded', () => {
		// An answer from before the model was kept. Guessing one would name a
		// model that may never have written it.
		expect(modelOf([question, answer(null)])).toBe('');
		expect(modelOf([])).toBe('');
	});

	it('handles a model served by the deployment rather than a provider', () => {
		expect(modelOf([question, answer('qwen3:8b', null)])).toBe('qwen3:8b');
	});
});
