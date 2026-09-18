import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

const packageRoot = fileURLToPath(new URL("..", import.meta.url));
const temporaryRoots: string[] = [];

afterEach(() => {
	for (const root of temporaryRoots.splice(0)) rmSync(root, { force: true, recursive: true });
});

describe("strict model generation", () => {
	it("preserves Kimi Coding models from the renamed regional catalog", () => {
		const fixtureRoot = mkdtempSync(join(tmpdir(), "diffex-generate-kimi-"));
		temporaryRoots.push(fixtureRoot);
		const isolatedPackageRoot = join(fixtureRoot, "package");
		mkdirSync(isolatedPackageRoot);
		for (const entry of ["package.json", "scripts", "src"]) {
			cpSync(join(packageRoot, entry), join(isolatedPackageRoot, entry), { recursive: true });
		}
		const modelIds = [
			"deepseek-v4-flash-0731",
			"deepseek-v4-pro",
			"glm-5.2",
			"qwen3.6-flash",
			"qwen3.7-max",
			"qwen3.7-plus",
			"qwen3.8-max",
		];
		const catalog = {
			"alibaba-token-plan": {
				models: Object.fromEntries(modelIds.map((id) => [id, { id, name: id, tool_call: true }])),
			},
			"kimi-code-plan-cn": {
				models: {
					"kimi-for-coding": {
						id: "kimi-for-coding",
						name: "Kimi For Coding",
						tool_call: true,
						reasoning: true,
						limit: { context: 1048576, output: 32768 },
					},
				},
			},
		};
		const preloadPath = join(fixtureRoot, "mock-catalog.mjs");
		writeFileSync(
			preloadPath,
			`const catalog = ${JSON.stringify(catalog)};\n` +
				`globalThis.fetch = async (input) => new Response(JSON.stringify(\n` +
				`  String(input) === "https://models.dev/api.json" ? catalog : { data: [] }\n` +
				`), { status: 200 });\n`,
		);
		const result = spawnSync(
			process.execPath,
			["--import", pathToFileURL(preloadPath).href, "scripts/generate-models.ts", "--strict"],
			{ cwd: isolatedPackageRoot, encoding: "utf8", timeout: 10_000 },
		);
		expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0);
		const models = JSON.parse(readFileSync(join(isolatedPackageRoot, "src/providers/data/kimi-coding.json"), "utf8"));
		expect(models["anthropic-messages"]["kimi-for-coding"]).toMatchObject({
			provider: "kimi-coding",
			baseUrl: "https://api.kimi.com/coding",
			contextWindow: 1048576,
			maxTokens: 32768,
			compat: { forceAdaptiveThinking: true, allowEmptySignature: true },
		});
	});

	it("fails before mutating generated data when an Individual model loses tool support", () => {
		const fixtureRoot = mkdtempSync(join(tmpdir(), "diffex-generate-models-"));
		temporaryRoots.push(fixtureRoot);
		const isolatedPackageRoot = join(fixtureRoot, "package");
		mkdirSync(isolatedPackageRoot);
		for (const entry of ["package.json", "scripts", "src"]) {
			cpSync(join(packageRoot, entry), join(isolatedPackageRoot, entry), { recursive: true });
		}
		const preloadPath = join(fixtureRoot, "mock-models-dev.mjs");
		const modelIds = [
			"deepseek-v4-flash-0731",
			"deepseek-v4-pro",
			"glm-5.2",
			"qwen3.6-flash",
			"qwen3.7-max",
			"qwen3.7-plus",
			"qwen3.8-max",
			"qwen3.8-max-preview",
		];
		const sourceModels = Object.fromEntries(
			modelIds.map((id) => [
				id,
				{
					id,
					name: id,
					tool_call: id !== "deepseek-v4-flash-0731",
				},
			]),
		);
		const catalog = { "alibaba-token-plan": { models: sourceModels } };
		writeFileSync(
			preloadPath,
			`const catalog = ${JSON.stringify(catalog)};\n` +
				`globalThis.fetch = async (input) => {\n` +
				`  if (String(input) === "https://models.dev/api.json") {\n` +
				`    return new Response(JSON.stringify(catalog), { status: 200 });\n` +
				`  }\n` +
				`  throw new Error(\`Unexpected fetch: \${String(input)}\`);\n` +
				`};\n`,
		);

		const generatedPaths = [
			"src/models.generated.ts",
			"src/providers/qwen-token-plan-individual.models.ts",
			"src/providers/data/qwen-token-plan-individual.json",
			"src/providers/data/.manifest.json",
		];
		const sourceBefore = generatedPaths.map((path) => readFileSync(join(packageRoot, path), "utf8"));
		const isolatedBefore = generatedPaths.map((path) => readFileSync(join(isolatedPackageRoot, path), "utf8"));

		const result = spawnSync(
			process.execPath,
			["--import", pathToFileURL(preloadPath).href, "scripts/generate-models.ts", "--strict"],
			{
				cwd: isolatedPackageRoot,
				encoding: "utf8",
				timeout: 10_000,
			},
		);

		expect(result.status).toBe(1);
		expect(`${result.stdout}\n${result.stderr}`).toContain(
			"qwen-token-plan-individual model IDs do not match (missing: deepseek-v4-flash-0731)",
		);
		expect(generatedPaths.map((path) => readFileSync(join(isolatedPackageRoot, path), "utf8"))).toEqual(
			isolatedBefore,
		);
		expect(generatedPaths.map((path) => readFileSync(join(packageRoot, path), "utf8"))).toEqual(sourceBefore);
	});
});
