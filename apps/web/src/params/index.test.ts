import { describe, expect, it } from 'vitest';
import { params } from './index';

function accepts(value: string): boolean {
	const result = params.uuid['~standard'].validate(value);
	return !(result instanceof Promise) && result.issues === undefined;
}

describe('the uuid matcher', () => {
	it('accepts an id', () => {
		expect(accepts('0b6c0f7e-9a35-4d4e-8f39-2c1f2f7c1a10')).toBe(true);
	});

	it('refuses anything that could reach further into a service path', () => {
		expect(accepts('..')).toBe(false);
		expect(accepts('0b6c0f7e-9a35-4d4e-8f39-2c1f2f7c1a10/../x')).toBe(false);
	});
});
