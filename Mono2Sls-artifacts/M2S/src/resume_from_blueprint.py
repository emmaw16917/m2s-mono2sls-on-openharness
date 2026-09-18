"""Resume the Mono2Sls pipeline from an arbitrary task position.

Usage (run from the M2S/ directory so that ROOT_DIR-relative paths resolve):

    python -m src.resume_from_blueprint --from-task 4

Task positions are 1-based and follow the order in src/config/tasks.yaml:

    1 architect_blueprint                 (architect)
    2 generate_complete_application       (code_developer)
    3 generate_shared_resources_template  (sam_engineer)
    4 generate_functions_template         (sam_engineer)
    5 assemble_final_template             (sam_engineer)
    6 validate_consistency                (consistency_validator)

Importing src.main applies the LLM throttle-retry patch, the inter-task
cooldown patch and the code-generation artifact validation, so resuming the
crew here behaves exactly like a full `python -m src.main` run.
"""

import argparse
import sys
import time
import traceback
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import src.main as pipeline  # noqa: E402  (applies patches at import time)
from crewai import Crew  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-task",
        type=int,
        default=4,
        help="1-based position of the first task to run (default: 4).",
    )
    args = parser.parse_args()

    blueprint = ROOT_DIR / "storage" / "blueprint.json"
    if not blueprint.exists():
        raise RuntimeError(
            "storage/blueprint.json not found. Run `python -m src.main` first."
        )

    agents = pipeline.build_agents()
    tasks = pipeline.build_tasks(agents)

    if not 1 <= args.from_task <= len(tasks):
        raise ValueError(
            f"--from-task must be between 1 and {len(tasks)}, got {args.from_task}."
        )

    resume_tasks = tasks[args.from_task - 1 :]

    print("=" * 60)
    print(f"Mono2Sls - Resuming from storage/blueprint.json (skip architect)")
    print("=" * 60)
    print(
        f"\nResuming with {len(resume_tasks)} tasks "
        f"(tasks {args.from_task}..{len(tasks)}).\n"
    )

    crew = Crew(
        agents=list(agents.values()),
        tasks=resume_tasks,
        verbose=True,
        memory=False,
        embedder=pipeline.OLLAMA_EMBEDDER_CONFIG,
    )

    workflow_start = time.time()
    try:
        crew.kickoff()
    except BaseException as e:
        error_msg = f"\nCRITICAL ERROR: {str(e)}\n\n{traceback.format_exc()}"
        print(error_msg)

        crash_log = ROOT_DIR / "crash_report.log"
        crash_log.write_text(error_msg, encoding="utf-8")
        print(f"\nCrash report saved to: {crash_log}")
        raise

    duration = time.time() - workflow_start
    print("\n" + "=" * 60)
    print("Resumed workflow completed!")
    print("=" * 60)
    print(f"Total resumed time: {duration:.2f}s ({duration / 60:.2f}min)")


if __name__ == "__main__":
    main()
