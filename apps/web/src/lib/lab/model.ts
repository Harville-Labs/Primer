import type { LabModelStatus } from '#lib/api/types.ts';

/** What a reader calls the resident model: its Hub repository, or its path. */
export function modelName(model: LabModelStatus): string {
	return model.reference.source === 'hub' ? model.reference.repository : model.reference.path;
}
