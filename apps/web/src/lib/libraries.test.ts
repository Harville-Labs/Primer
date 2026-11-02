import { describe, expect, it } from 'vitest';
import type { Principal } from '#lib/api/types.ts';
import { owns } from './libraries';

const ME = { user_id: 'u-1' } as Principal;

describe('owning a library', () => {
	it('is true only for the caller’s own', () => {
		expect(owns(ME, { owner_user_id: 'u-1' })).toBe(true);
		expect(owns(ME, { owner_user_id: 'u-2' })).toBe(false);
	});

	it('is false when the caller is unknown, so nothing is offered', () => {
		expect(owns(null, { owner_user_id: 'u-1' })).toBe(false);
	});
});
