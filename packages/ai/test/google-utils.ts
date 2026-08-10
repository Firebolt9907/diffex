import { getModels } from "../src/compat.ts";
import type { Model } from "../src/types.ts";

export function getGoogleTextModel(): Model<"google-generative-ai"> {
	const preferredModelIds: string[] = ["gemini-2.5-flash"];
	const models = getModels("google");
	const model =
		models.find((candidate) => preferredModelIds.includes(candidate.id)) ??
		models.find(
			(candidate) =>
				candidate.id.startsWith("gemini-") &&
				candidate.input.includes("text") &&
				!candidate.id.includes("computer-use") &&
				!candidate.id.includes("image") &&
				!candidate.id.includes("live"),
		);

	if (!model) {
		throw new Error("No Google Gemini text model available");
	}

	return model;
}
