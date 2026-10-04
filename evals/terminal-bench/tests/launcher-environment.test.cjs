// Run in a disposable Linux container: this fixture owns /usr/local/bin/diffex.
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } = require("node:fs");
const { join, resolve } = require("node:path");
const { test } = require("node:test");

test("real launcher preserves task imports and caches while isolating Diffex state", {
	skip: process.env.DIFFEX_LAUNCHER_CONTAINER_TEST !== "1",
}, (t) => {
	assert.equal(existsSync("/usr/local/bin/diffex"), false, "Use a disposable container without Diffex installed");
	const root = mkdtempSync("/tmp/launcher-env-");
	t.after(() => {
		rmSync("/usr/local/bin/diffex");
		rmSync(root, { recursive: true, force: true });
	});
	const home = join(root, "task-home");
	const source = join(root, "src");
	const cache = join(root, "deno-cache");
	for (const directory of [home, source, cache]) mkdirSync(directory);
	writeFileSync(join(source, "task_module.py"), 'VALUE = "import-ok"\n');
	writeFileSync(join(cache, "metadata"), "cache-ok");
	writeFileSync(join(home, "home-cache"), "home-ok");
	writeFileSync("/usr/local/bin/diffex", `#!/bin/sh
set -eu
python3 -c 'import task_module; print(task_module.VALUE)'
cat "$DENO_DIR/metadata" "$HOME/home-cache"
test -z "\${ANTHROPIC_API_KEY:-}"
test -z "\${DIFFEX_MODEL:-}"
test "$OPENAI_API_KEY" = selected-fixture
test "$DIFFEX_OFFLINE" = 1
test "$DIFFEX_CODING_AGENT_DIR" != /stale
`, { mode: 0o755 });
	const run = join(root, "run");
	mkdirSync(run);
	writeFileSync(join(run, "requested.json"), JSON.stringify({
		provider: "openai", model: "fixture", thinking: "high", tools: ["bash"],
		credential_env_names: ["OPENAI_API_KEY"], provider_env_names: ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
		task_environment: "preserve", background_process_lifetime: "environment", user_message_suffix: "fixture",
	}));
	const result = spawnSync(process.execPath, [resolve(__dirname, "../src/diffex_terminal_bench/run.cjs"), run], {
		encoding: "utf8", env: {
			PATH: process.env.PATH, HOME: home, PYTHONPATH: source, DENO_DIR: cache,
			DIFFEX_TB_PROVIDER: "openai", DIFFEX_TB_MODEL: "fixture", DIFFEX_TB_THINKING: "high",
			DIFFEX_MODEL: "stale", DIFFEX_CODING_AGENT_DIR: "/stale", DIFFEX_OFFLINE: "0",
			OPENAI_API_KEY: "selected-fixture", ANTHROPIC_API_KEY: "unselected-fixture",
		},
	});
	const state = JSON.parse(readFileSync(join(run, "state.json"), "utf8"));
	t.after(() => rmSync(state.state_dir, { recursive: true, force: true }));
	assert.equal(result.status, 0, result.stderr + readFileSync(join(run, "stderr.log"), "utf8"));
	assert.equal(readFileSync(join(run, "stdout.jsonl"), "utf8"), "import-ok\ncache-okhome-ok");
	assert.deepEqual(JSON.parse(readFileSync(join(state.state_dir, "settings.json"), "utf8")), { evolution: { enabled: false } });
});
