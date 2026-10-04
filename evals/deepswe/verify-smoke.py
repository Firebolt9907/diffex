"""Qualify smoke execution and evidence independently of task correctness."""

import argparse
import json
from pathlib import Path
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    args = parser.parse_args()
    job = args.job.resolve()
    summary = json.loads((job / "result.json").read_text())
    trials = list(job.glob("*/result.json"))
    if summary.get("finished_at") is None or summary["n_total_trials"] != 1 or len(trials) != 1:
        raise RuntimeError("Expected one finished smoke trial.")
    result = json.loads(trials[0].read_text())
    trial = trials[0].parent
    if result["task_name"] != "datacurve/abs-stepped-slices":
        raise RuntimeError("The job is not the selected DeepSWE smoke task.")
    if result.get("exception_info"):
        exception = result["exception_info"]
        raise RuntimeError(f"Smoke execution failed: {exception['exception_type']}: {exception['exception_message']}")
    if result.get("verifier_environment_mode") != "separate" or not result.get("verifier_result"):
        raise RuntimeError("Separate verification did not complete.")
    metadata = result["agent_result"]["metadata"]
    outcome = metadata["diffex_outcome"]
    if outcome["status"] != "completed" or outcome.get("agent_execution_stopped") is not True:
        raise RuntimeError("Agent completion and shutdown must be confirmed.")
    if metadata.get("diffex_accounting", {}).get("complete") is not True:
        raise RuntimeError("Token accounting is incomplete.")
    run = trial / "agent" / Path(metadata["diffex_run_dir"]).name
    requested = json.loads((run / "requested.json").read_text())
    effective = json.loads((run / "effective.json").read_text())
    if (requested["model"], requested["thinking"], requested["subagents"]) != ("gpt-6-luna", "max", "disabled"):
        raise RuntimeError("Unexpected recorded execution settings.")
    if requested.get("evolution") != {"enabled": False}:
        raise RuntimeError("Evolution was not disabled in the requested settings.")
    if requested.get("task_environment") != "preserve":
        raise RuntimeError("The launcher did not request task environment preservation.")
    if effective.get("model") != "gpt-6-luna" or effective.get("thinking") != "max":
        raise RuntimeError("Effective model/reasoning differs from the request.")
    if effective.get("context_files_disabled") is not True or effective.get("instruction_delivered") is not True:
        raise RuntimeError("Prompt isolation evidence is incomplete.")
    resources = [json.loads(p.read_text()) for p in (trial / "artifacts/e2b-resources").glob("*.json")]
    if len(resources) != 2 or len({r["sandbox_id"] for r in resources}) != 2 or not all(r["verified"] for r in resources):
        raise RuntimeError("Expected resource checks for two distinct agent/verifier sandboxes.")
    runtime = [json.loads(p.read_text()) for p in (trial / "artifacts/e2b-runtime").glob("*.json")]
    if len(runtime) != 2 or not all(r["verified"] for r in runtime):
        raise RuntimeError("Expected image environment checks for agent and verifier sandboxes.")
    patches = list((trial / "artifacts").rglob("model.patch"))
    if not patches:
        raise RuntimeError("The commit-derived model.patch artifact is missing.")
    trajectory = trial / "agent/trajectory.json"
    subprocess.run([sys.executable, "-m", "harbor.utils.trajectory_validator", str(trajectory)], check=True)
    report = {
        "status": "passed", "trial": result["trial_name"],
        "verifier_rewards": result["verifier_result"]["rewards"],
        "patch_bytes": [p.stat().st_size for p in patches],
        "sandbox_count": len(resources), "accounting_complete": True,
        "meaning": "Execution and evidence qualified; verifier rewards separately describe task correctness.",
    }
    (job / "smoke-check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
