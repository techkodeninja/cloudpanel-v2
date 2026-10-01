"""Test helpers: put stand-in commands on PATH and read what they received."""

import json
import os
import tempfile
from pathlib import Path

RECORDER = Path(__file__).parent / "fakes" / "recorder.py"
SSH_KEYGEN = Path(__file__).parent / "fakes" / "ssh_keygen.py"

# Fakes that do more than record (they're used under these command names).
SPECIAL = {"ssh-keygen": SSH_KEYGEN}


class FakeCommands:
    """Context manager: commands named in `names` become recorders.

        with FakeCommands("clpctl", "sudo") as fakes:
            ...code that runs clpctl...
            fakes.calls  # [{"argv": [...], "cwd": ..., "stdin": ...}, ...]
    """

    def __init__(self, *names, output="", exit_code=0, error="fake failure"):
        self.names = names
        self.env = {
            "CP_FAKE_OUTPUT": output,
            "CP_FAKE_EXIT": str(exit_code),
            "CP_FAKE_ERROR": error,
        }

    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        bin_dir = Path(self._dir.name) / "bin"
        bin_dir.mkdir()
        os.chmod(RECORDER, 0o755)
        os.chmod(SSH_KEYGEN, 0o755)
        for name in self.names:
            (bin_dir / name).symlink_to(SPECIAL.get(name, RECORDER))
        self.log = Path(self._dir.name) / "calls.log"
        self.log.touch()

        self._saved = {key: os.environ.get(key) for key in ["PATH", "CP_TEST_LOG", *self.env]}
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        os.environ["CP_TEST_LOG"] = str(self.log)
        os.environ.update(self.env)
        return self

    def __exit__(self, *exc):
        self._final_calls = self._read_calls()  # keep them after the folder is gone
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._dir.cleanup()

    def _read_calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    @property
    def calls(self):
        if hasattr(self, "_final_calls"):
            return self._final_calls
        return self._read_calls()

    @property
    def argvs(self):
        return [call["argv"] for call in self.calls]
