import type { ChatModel, LabStatus } from '#lib/api/types.ts';
import { chatFor } from '#lib/server/chat.ts';
import { labFor } from '#lib/server/lab.ts';
import { attempt } from '#lib/server/problem.ts';
import type { PageServerLoad } from './$types';

/**
 * What can be scored: Chat's models, and Lab's resident one.
 *
 * Each source fails on its own. An endpoint that is down leaves Lab to
 * score with, and the reverse, so neither failing is a page that will not
 * load; each problem is said beside the picker in its service's own words.
 */
export const load: PageServerLoad = async ({ request, fetch, parent }) => {
	const { capabilities } = await parent();
	const [chat, lab] = await Promise.all([
		capabilities.chat_available
			? attempt(() => chatFor(request, fetch).models())
			: Promise.resolve(null),
		capabilities.lab_available
			? attempt(() => labFor(request, fetch).status())
			: Promise.resolve(null),
	]);
	const models: ChatModel[] = chat && 'value' in chat ? (chat.value.models ?? []) : [];
	const status: LabStatus | null = lab && 'value' in lab ? lab.value : null;
	return {
		models,
		chatProblem: chat && 'problem' in chat ? chat.problem : null,
		status,
		labProblem: lab && 'problem' in lab ? lab.problem : null,
	};
};
