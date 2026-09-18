import { describe, expect, it } from "vitest";
import { lazyApi } from "../src/api/lazy.ts";
import { createModels } from "../src/models.ts";
import { fauxProvider } from "../src/providers/faux.ts";
import type { AssistantMessageEvent, DeferredHandle } from "../src/types.ts";

describe("request setup cancellation", () => {
	it.each(["stream", "streamSimple", "fetchDeferred"] as const)(
		"classifies an explicitly cancelled %s setup as aborted",
		async (method) => {
			const faux = fauxProvider();
			const models = createModels();
			models.setProvider(faux.provider);
			const model = faux.getModel();
			const controller = new AbortController();
			controller.abort();
			const options = { signal: controller.signal };
			const handle: DeferredHandle = { provider: model.provider, modelId: model.id, api: model.api, id: "pending" };
			const message =
				method === "fetchDeferred"
					? await models.fetchDeferred(model, handle, options)
					: await models[method](model, { messages: [] }, options).result();
			expect(message.stopReason).toBe("aborted");
			expect(faux.state.callCount).toBe(0);
			expect(faux.state.deferredFetchCount).toBe(0);
		},
	);

	it.each([false, true])("only classifies cancellation when the caller signal is aborted: %s", async (abort) => {
		const faux = fauxProvider();
		const models = createModels();
		const controller = new AbortController();
		models.setProvider({
			...faux.provider,
			auth: {
				apiKey: {
					name: "Test",
					resolve: async () => {
						if (abort) controller.abort();
						throw new Error("This operation was aborted");
					},
				},
			},
		});
		const stream = models.streamSimple(faux.getModel(), { messages: [] }, { signal: controller.signal });
		const events: AssistantMessageEvent[] = [];
		for await (const event of stream) events.push(event);
		const reason = abort ? "aborted" : "error";
		expect(await stream.result()).toMatchObject({ stopReason: reason });
		expect(events).toMatchObject([{ type: "error", reason, error: { stopReason: reason } }]);
		expect(faux.state.callCount).toBe(0);
	});

	it("propagates cancellation through lazy API loading", async () => {
		const faux = fauxProvider();
		const controller = new AbortController();
		const api = lazyApi(async () => {
			controller.abort();
			throw controller.signal.reason;
		});
		const message = await api.streamSimple(faux.getModel(), { messages: [] }, { signal: controller.signal }).result();
		expect(message.stopReason).toBe("aborted");
	});
});
