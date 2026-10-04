"""Offline regressions for image metadata, E2B propagation, and timeout evidence."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from e2b.sandbox_async.commands.command import Commands
from harbor.environments.base import ExecResult
from harbor.environments.e2b import E2BEnvironment
from harbor.environments.factory import EnvironmentFactory
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.config import EnvironmentConfig as TrialEnvironmentConfig
from harbor.models.trial.paths import TrialPaths
from packaging.version import Version

from deepswe_e2b import ResourceCheckedE2B, image_runtime, prepare_image_runtimes, registry_get


IMAGE = "public.ecr.aws/example/tasks:fixture"


class ImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sleep = patch("deepswe_e2b.registry_sleep", new_callable=AsyncMock)
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    async def test_throttled_manifest_is_retried_without_restarting_trial(self):
        config = json.dumps({"os": "linux", "architecture": "amd64", "config": {"Env": ["PATH=/bin"]}}).encode()
        digest = "sha256:" + hashlib.sha256(config).hexdigest()
        manifest_requests = 0

        def respond(request):
            nonlocal manifest_requests
            if request.url.path == "/token/":
                return httpx.Response(200, json={"token": "fixture"})
            if "/manifests/" in request.url.path:
                manifest_requests += 1
                if manifest_requests == 1:
                    return httpx.Response(429, headers={"Retry-After": "2"})
                return httpx.Response(200, json={"config": {"digest": digest}})
            return httpx.Response(200, content=config)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            runtime = await image_runtime(client, IMAGE)
        self.assertEqual(runtime["env"], {"PATH": "/bin"})
        self.assertEqual(manifest_requests, 2)
        self.assertIn(2, [call.args[0] for call in self.sleep.await_args_list])

    async def test_permanent_errors_and_exhausted_throttling_are_bounded(self):
        for status, count in ((404, 1), (429, 6), (503, 6)):
            calls = []

            def respond(request):
                calls.append(request)
                return httpx.Response(status)

            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                with self.assertRaises(httpx.HTTPStatusError):
                    await registry_get(client, "https://public.ecr.aws/fixture")
            self.assertEqual(len(calls), count)

    async def test_long_retry_after_fails_preparation_instead_of_retrying_early(self):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(429, headers={"Retry-After": "120"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                await registry_get(client, "https://public.ecr.aws/fixture")
        self.assertEqual(len(calls), 1)

    async def test_transport_failure_retries_safe_read(self):
        calls = []

        def respond(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ReadTimeout("fixture timeout", request=request)
            return httpx.Response(200, json={"ok": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            self.assertEqual((await registry_get(client, "https://public.ecr.aws/fixture")).json(), {"ok": True})
        self.assertEqual(len(calls), 2)

    async def test_registry_config_and_amd64_selection(self):
        config = json.dumps({"os": "linux", "architecture": "amd64", "config": {
            "Env": ["PYTHONPATH=/app/src", "DENO_DIR=/deno-cache", "VALUE=a=b"], "WorkingDir": "/app",
        }}).encode()
        config_digest = "sha256:" + hashlib.sha256(config).hexdigest()
        manifest = json.dumps({"config": {"digest": config_digest}}).encode()
        manifest_digest = "sha256:" + hashlib.sha256(manifest).hexdigest()

        def respond(request):
            if request.url.path == "/token/":
                return httpx.Response(200, json={"token": "fixture-token"})
            self.assertEqual(request.headers["Authorization"], "Bearer fixture-token")
            if request.url.path.endswith("/manifests/fixture"):
                return httpx.Response(200, json={"manifests": [
                    {"platform": {"os": "linux", "architecture": "arm64"}, "digest": "wrong"},
                    {"platform": {"os": "linux", "architecture": "amd64"}, "digest": manifest_digest},
                ]})
            if request.url.path.endswith("/manifests/" + manifest_digest):
                return httpx.Response(200, content=manifest)
            self.assertTrue(request.url.path.endswith("/blobs/" + config_digest))
            return httpx.Response(200, content=config)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            runtime = await image_runtime(client, IMAGE)
        self.assertEqual(runtime["env"], {"PYTHONPATH": "/app/src", "DENO_DIR": "/deno-cache", "VALUE": "a=b"})
        self.assertEqual(runtime["workdir"], "/app")
        self.assertEqual(runtime["manifest_digest"], manifest_digest)

    async def test_corrupt_config_is_rejected(self):
        def respond(request):
            if request.url.path == "/token/":
                return httpx.Response(200, json={"token": "fixture"})
            if "/manifests/" in request.url.path:
                return httpx.Response(200, json={"config": {"digest": "sha256:" + "0" * 64}})
            return httpx.Response(200, json={"config": {}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaisesRegex(ValueError, "configuration digest mismatch"):
                await image_runtime(client, IMAGE)

    async def test_unversioned_and_non_ecr_images_are_rejected(self):
        async with httpx.AsyncClient() as client:
            for image in ("public.ecr.aws/example/task", "registry.example/task:tag"):
                with self.assertRaises(ValueError):
                    await image_runtime(client, image)


class EnvironmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tests = self.root / "tests"
        self.tests.mkdir()
        (self.tests / "Dockerfile").write_text(f"FROM {IMAGE}\nCOPY test.sh /tests/test.sh\nRUN chmod +x /tests/test.sh\n")
        self.paths = TrialPaths(self.root / "trial")

    def environment(self, verifier=False):
        return ResourceCheckedE2B(
            environment_dir=self.tests, environment_name="fixture", session_id="fixture",
            trial_paths=self.paths, task_env_config=EnvironmentConfig(
                docker_image=None if verifier else IMAGE, cpus=2, memory_mb=8192,
                storage_mb=20480, env={"OVERRIDE": "task"},
            ),
        )

    async def test_agent_and_verifier_restore_runtime_before_start_and_exec(self):
        for verifier in (False, True):
            environment = self.environment(verifier)
            source = self.root / "src"
            source.mkdir(exist_ok=True)
            (source / "fixture_import.py").write_text("VALUE = 'import-ok'\n")
            runtime = {"image": IMAGE, "manifest_digest": "fixture", "config_digest": "fixture",
                       "workdir": str(self.root), "env": {"PYTHONPATH": str(source), "DENO_DIR": "/deno-cache", "OVERRIDE": "image"}}

            async def start(instance, force_build):
                self.assertEqual(instance._startup_env()["PYTHONPATH"], str(source))
                instance._sandbox = SimpleNamespace(sandbox_id="fixture-sandbox")

            async def dispatch(command, *, cwd, env, timeout_sec, user):
                process = await asyncio.create_subprocess_exec(
                    "/bin/sh", "-c", command, cwd=cwd, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )

                async def wait():
                    stdout, stderr = await process.communicate()
                    return SimpleNamespace(stdout=stdout.decode(), stderr=stderr.decode(), exit_code=process.returncode)

                return SimpleNamespace(wait=wait)

            response = httpx.Response(200, request=httpx.Request("GET", "https://api.e2b.dev"),
                                      json={"cpuCount": 2, "memoryMB": 8192, "diskSizeMB": 20480})
            with patch.object(environment, "_load_runtime", return_value=runtime), \
                    patch.object(E2BEnvironment, "start", start), \
                    patch.object(environment, "_dispatch_command", side_effect=dispatch), \
                    patch("httpx.AsyncClient.get", AsyncMock(return_value=response)), \
                    patch.dict("os.environ", {"E2B_API_KEY": "fixture"}):
                await environment.start(False)
                result = await environment.exec("python3 -c 'import fixture_import; print(fixture_import.VALUE)'")
                self.assertEqual(result.return_code, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "import-ok")
                self.assertEqual(environment._merge_env({"OVERRIDE": "exec"})["OVERRIDE"], "exec")
                self.assertEqual(environment._startup_env()["OVERRIDE"], "task")
            record = json.loads((self.paths.trial_dir / "artifacts/e2b-runtime/fixture-sandbox.json").read_text())
            self.assertTrue(record["verified"])
            self.assertNotIn("env", record)

    async def test_lost_environment_fails_before_model_execution(self):
        environment = self.environment()
        runtime = {"image": IMAGE, "workdir": "/app", "env": {"PYTHONPATH": "/app/src"}}

        async def start(instance, force_build):
            instance._sandbox = SimpleNamespace(sandbox_id="fixture")

        with patch.object(environment, "_load_runtime", return_value=runtime), \
                patch.object(E2BEnvironment, "start", start), \
                patch.object(environment, "exec", AsyncMock(return_value=ExecResult(stdout="", return_code=0))):
            with self.assertRaisesRegex(RuntimeError, "preserve image environment"):
                await environment.start(False)

    async def test_sdk_login_shell_preserves_task_path_and_override_precedence(self):
        # Exercise the pinned SDK's real run/_start command construction, then
        # execute its ProcessConfig locally in place of the remote envd RPC.
        environment = self.environment()
        profile = self.root / "shell-env"
        profile.write_text("export PATH=/usr/bin:/bin\n")
        paths = []
        for label in ("image", "exec", "scoped"):
            directory = self.root / f"{label} tools with 'quotes' $literal"
            directory.mkdir()
            executable = directory / "task-only-tool"
            executable.write_text(f"#!/bin/sh\nprintf '%s\\n' {label}\n")
            executable.chmod(0o755)
            paths.append(f"{directory}:/usr/bin:/bin")
        environment._persistent_env = {"PATH": paths[0], "BASH_ENV": str(profile)}
        commands = object.__new__(Commands)
        commands._connection_config = SimpleNamespace(get_request_timeout=lambda value: 30)
        commands._envd_version = Version("0.5.0")
        commands._rpc = SimpleNamespace(start=lambda request, **kwargs: request.process)
        environment._sandbox = SimpleNamespace(commands=commands)
        process = None

        async def start(config, timeout):
            nonlocal process
            self.assertEqual(config.cmd, "/bin/bash")
            self.assertEqual(list(config.args[:2]), ["-l", "-c"])
            process = await asyncio.create_subprocess_exec(
                config.cmd, *config.args, cwd=config.cwd or None,
                env={**os.environ, **config.envs},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )

        async def wait():
            stdout, stderr = await process.communicate()
            return SimpleNamespace(stdout=stdout.decode(), stderr=stderr.decode(), exit_code=process.returncode)

        with patch("e2b.sandbox_async.commands.command.as_stream", side_effect=lambda config: config), \
                patch("e2b.sandbox_async.commands.command.first_event", side_effect=start), \
                patch("e2b.sandbox_async.commands.command.extract_start_pid", return_value=1234), \
                patch("e2b.sandbox_async.commands.command.AsyncCommandHandle", return_value=SimpleNamespace(wait=wait)):
            command = 'task-only-tool && printf "%s\\n" "$PATH"'
            result = await environment.exec(command, cwd=str(self.root))
            self.assertEqual(result.return_code, 0, result.stderr)
            self.assertEqual(result.stdout, f"image\n{paths[0]}\n")
            result = await environment.exec(command, cwd=str(self.root), env={"PATH": paths[1]})
            self.assertEqual(result.stdout, f"exec\n{paths[1]}\n")
            with environment.scoped_exec_env({"PATH": paths[2]}):
                result = await environment.exec(command, cwd=str(self.root), env={"PATH": paths[1]})
            self.assertEqual(result.stdout, f"scoped\n{paths[2]}\n")

    async def test_verifier_logs_are_recovered_before_deletion(self):
        environment = self.environment(True)
        environment._sandbox = SimpleNamespace(sandbox_id="timed-out")
        events = []

        async def download(source, target):
            events.append("download")
            target.mkdir(parents=True)
            (target / "test-stdout.txt").write_text("last test before timeout\n")

        async def stop(instance, delete):
            events.append("delete")

        with patch.object(environment, "download_dir", side_effect=download), patch.object(E2BEnvironment, "stop", stop):
            await environment.stop(True)
        self.assertEqual(events, ["download", "delete"])
        recovery = self.paths.trial_dir / "artifacts/verifier-recovery/timed-out"
        self.assertTrue(json.loads((recovery / "recovery.json").read_text())["complete"])
        self.assertIn("last test", (recovery / "logs/test-stdout.txt").read_text())
        self.assertFalse(self.paths.reward_json_path.exists())

    async def test_failed_recovery_still_deletes_sandbox(self):
        environment = self.environment(True)
        environment._sandbox = SimpleNamespace(sandbox_id="failed-download")
        with patch.object(environment, "download_dir", AsyncMock(side_effect=TimeoutError)), \
                patch.object(E2BEnvironment, "stop", AsyncMock()) as stop, self.assertLogs(level="WARNING"):
            await environment.stop(True)
        stop.assert_awaited_once_with(True)
        record = self.paths.trial_dir / "artifacts/verifier-recovery/failed-download/recovery.json"
        self.assertEqual(json.loads(record.read_text()), {"complete": False, "error": "TimeoutError"})

    def test_verifier_runtime_changes_are_not_silently_ignored(self):
        (self.tests / "Dockerfile").write_text(f"FROM {IMAGE}\nENV PYTHONPATH=/other\n")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.environment(True)._runtime_image()

    async def test_eight_preparers_share_cache_and_agent_verifier_startup_is_offline(self):
        catalog = self.root / "runtime-images.json"
        runtime = {"image": IMAGE, "env": {"PATH": "/bin"}, "workdir": "/app"}
        with patch("deepswe_e2b.image_runtime", AsyncMock(return_value=runtime)) as fetch:
            digests = await asyncio.gather(*(prepare_image_runtimes([IMAGE], catalog) for _ in range(8)))
            fetch.assert_awaited_once()
            self.assertEqual(len(set(digests)), 1)
            self.assertEqual(await prepare_image_runtimes([IMAGE], catalog), digests[0])
            fetch.assert_awaited_once()
        config = TrialEnvironmentConfig(import_path="deepswe_e2b:ResourceCheckedE2B", kwargs={
            "runtime_catalog": str(catalog), "runtime_catalog_sha256": digests[0],
        })
        environments = [EnvironmentFactory.create_environment_from_config(
            config=config, environment_dir=self.tests, environment_name="fixture", session_id="fixture",
            trial_paths=self.paths, task_env_config=EnvironmentConfig(
                docker_image=None if index % 2 == 0 else IMAGE, cpus=2, memory_mb=8192,
                storage_mb=20480, env={"OVERRIDE": "task"},
            ),
        ) for index in range(8)]
        self.assertTrue(all(environment._load_runtime() == runtime for environment in environments))

        async def start(instance, force_build):
            instance._sandbox = SimpleNamespace(sandbox_id=str(id(instance)))

        async def resource_request(url, **kwargs):
            self.assertTrue(url.startswith("https://api.e2b.dev/sandboxes/"), url)
            return httpx.Response(200, request=httpx.Request("GET", url),
                                  json={"cpuCount": 2, "memoryMB": 8192, "diskSizeMB": 20480})

        with patch.object(E2BEnvironment, "start", start), \
                patch.object(ResourceCheckedE2B, "exec", AsyncMock(return_value=ExecResult(
                    stdout="PATH=/bin\0OVERRIDE=task\0", return_code=0))), \
                patch("httpx.AsyncClient.get", side_effect=resource_request) as get, \
                patch.dict("os.environ", {"E2B_API_KEY": "fixture"}):
            await asyncio.gather(*(environment.start(False) for environment in environments))
        self.assertEqual(get.await_count, 8)
        catalog.write_text(catalog.read_text().replace("/bin", "/changed"))
        with self.assertRaisesRegex(ValueError, "catalog changed"):
            environments[0]._load_runtime()
        with patch("deepswe_e2b.image_runtime", AsyncMock()) as fetch:
            with self.assertRaisesRegex(ValueError, "integrity"):
                await prepare_image_runtimes([IMAGE], catalog)
            fetch.assert_not_awaited()

    async def test_failed_preparation_keeps_successes_and_does_not_cache_failure(self):
        catalog = self.root / "runtime-images.json"
        images = [IMAGE + "-a", IMAGE + "-b"]
        first = {"image": images[0], "env": {}, "workdir": "/app"}
        second = {"image": images[1], "env": {}, "workdir": "/app"}
        with patch("deepswe_e2b.image_runtime", AsyncMock(side_effect=[first, httpx.ReadTimeout("fixture")])):
            with self.assertRaises(httpx.ReadTimeout):
                await prepare_image_runtimes(images, catalog)
        self.assertEqual(set(json.loads(catalog.read_text())["images"]), {images[0]})
        with patch("deepswe_e2b.image_runtime", AsyncMock(return_value=second)) as fetch:
            await prepare_image_runtimes(images, catalog)
        self.assertEqual(fetch.await_args.args[1], images[1])
        fetch.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
