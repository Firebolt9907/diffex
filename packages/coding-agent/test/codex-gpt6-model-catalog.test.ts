import { InMemoryModelsStore } from "@diffexai/diffex-ai";
import { describe, expect, it, vi } from "vitest";
import { listModels } from "../src/cli/list-models.ts";
import { AuthStorage } from "../src/core/auth-storage.ts";
import { ModelRuntime } from "../src/core/model-runtime.ts";

describe("Codex GPT-6 model catalog", () => {
	it("lists GPT-6 models for a ChatGPT login", async () => {
		const modelRuntime = await ModelRuntime.create({
			credentials: AuthStorage.inMemory({
				"openai-codex": {
					type: "oauth",
					access: "fixture-token",
					refresh: "fixture-refresh",
					expires: Date.now() + 60 * 60 * 1000,
				},
			}),
			modelsPath: null,
			modelsStore: new InMemoryModelsStore(),
			allowModelNetwork: false,
			refreshOnCreate: false,
		});
		const output: string[] = [];
		const log = vi.spyOn(console, "log").mockImplementation((line: string) => {
			output.push(line);
		});

		try {
			await listModels(modelRuntime, "gpt-6");
		} finally {
			log.mockRestore();
		}

		const listedIds = output
			.filter((line) => line.startsWith("openai-codex  gpt-6"))
			.map((line) => line.trim().split(/\s+/)[1]);
		expect(listedIds).toEqual(["gpt-6-astra", "gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol"]);
	});
});
