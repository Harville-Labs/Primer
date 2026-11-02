import { defineParams } from '@sveltejs/kit/params';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Route parameter matchers, all in one module as SvelteKit 3 requires: it
 * reads this file and no longer looks for one file per matcher.
 *
 * A matcher returns the value to accept it and `undefined` to refuse it.
 */
export const params = defineParams({
	/**
	 * An id that can go into a service's path as it is.
	 *
	 * A route that does not match is SvelteKit's 404, before the handler runs,
	 * so nothing but an id is ever put in a path to Control.
	 */
	uuid: (param: string) => (UUID.test(param) ? param : undefined),
});
