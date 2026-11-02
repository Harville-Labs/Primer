import type { LibrarySummary, Principal } from '#lib/api/types.ts';

/**
 * Whether a library is the caller's own rather than one shared with them.
 *
 * Only decides what to offer; Control refuses the rest either way. False
 * when the principal is unknown, because the layout tolerates `/me`
 * failing and an unknown caller owns nothing.
 */
export function owns(
	principal: Principal | null,
	library: Pick<LibrarySummary, 'owner_user_id'>,
): boolean {
	return principal !== null && library.owner_user_id === principal.user_id;
}
