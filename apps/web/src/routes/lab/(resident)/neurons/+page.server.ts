import { residentStatus } from '#lib/server/lab.ts';
import type { PageServerLoad } from './$types';

/** Which model Lab has resident, for the card beside the prompt. */
export const load: PageServerLoad = ({ request, fetch }) => residentStatus(request, fetch);
