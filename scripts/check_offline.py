"""Repeatable local/CI verification, temporary DB, no credentials, no old result overwrite."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="ops-pilot-offline-") as directory:
        env = {**os.environ, "AGENT_MODE": "langgraph", "KNOWLEDGE_RETRIEVER": "candidate",
               "DEEPSEEK_API_KEY": "", "DATABASE_PATH": str(Path(directory) / "app.db"),
               "DEMO_AUTH_FILE": str(Path(directory) / "unconfigured-auth.json"),
               "OPS_EVAL_OUTPUT_DIR": str(ROOT / ".local-results" / "stage7"),
               "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false", "PYTHONUTF8": "1"}
        commands = [["-m", "pip", "check"], ["-m", "pytest", "-q"],
                    ["evals/run_live_agent_mock_smoke.py"], ["evals/run_local.py"],
                    ["scripts/validate_knowledge.py"],
                    ["-c", "from evals.run_rag_comparison import verify_inputs; verify_inputs(); print('Frozen inputs unchanged')"]]
        for command in commands:
            print("RUN python " + " ".join(command), flush=True)
            result = subprocess.run([sys.executable, *command], cwd=ROOT, env=env)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
