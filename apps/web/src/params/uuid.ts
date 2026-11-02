/**
 * An id that can go into a service's path as it is.
 *
 * A route that does not match is SvelteKit's 404, before the handler runs,
 * so nothing but an id is ever put in a path to Control.
 */
export function match(param: string): boolean {
	return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(param);
}
