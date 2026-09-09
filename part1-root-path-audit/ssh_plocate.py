from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import paramiko

from config import Settings
from mapping import collapse_slashes, normalize_relative_path, trim_trailing_slash

BATCH_BEGIN = "__PLOCATE_BEGIN__"
BATCH_END = "__PLOCATE_END__"


class PlocateClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.settings.ssh_host,
            port=self.settings.ssh_port,
            username=self.settings.ssh_username,
            password=self.settings.ssh_password,
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(15)
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "PlocateClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def resolve_paths(self, relative_paths: Sequence[str]) -> List[Optional[str]]:
        """Return absolute matched path (or None) for each relative path, same order."""
        if not relative_paths:
            return []
        if self._client is None:
            raise RuntimeError("SSH client is not connected")

        command = self._build_batch_command()
        stdin, stdout, stderr = self._client.exec_command(command, timeout=120)
        payload = "\n".join(relative_paths) + "\n"
        stdin.write(payload)
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_status = stdout.channel.recv_exit_status()
        blocks = self._parse_blocks(out, expected=len(relative_paths))
        if len(blocks) != len(relative_paths):
            raise RuntimeError(
                f"plocate batch size mismatch: expected={len(relative_paths)}, "
                f"got={len(blocks)}, exit={exit_status}, stderr={err[:400]!r}"
            )
        results: List[Optional[str]] = []
        for relative, block in zip(relative_paths, blocks):
            results.append(self._first_exact_under_roots(block, relative))
        return results

    def _build_batch_command(self) -> str:
        db = self.settings.plocate_db.strip()
        limit = self.settings.plocate_limit
        if db:
            plocate = f"plocate -d {self._shell_quote(db)} -l {limit} -- \"$rel\" 2>/dev/null || true"
        else:
            plocate = f"plocate -l {limit} -- \"$rel\" 2>/dev/null || true"
        return (
            "while IFS= read -r rel || [ -n \"$rel\" ]; do "
            f"echo {BATCH_BEGIN}; "
            f"if [ -n \"$rel\" ]; then {plocate}; fi; "
            f"echo {BATCH_END}; "
            "done"
        )

    @staticmethod
    def _shell_quote(value: str) -> str:
        return "'" + value.replace("'", "'\"'\"'") + "'"

    @staticmethod
    def _parse_blocks(output: str, expected: int) -> List[str]:
        blocks: List[str] = []
        current: Optional[List[str]] = None
        for raw in output.splitlines():
            line = raw.rstrip("\r")
            if line == BATCH_BEGIN:
                if current is not None:
                    raise RuntimeError("plocate batch protocol error: nested begin")
                current = []
            elif line == BATCH_END:
                if current is None:
                    raise RuntimeError("plocate batch protocol error: end without begin")
                blocks.append("\n".join(current).strip())
                current = None
            elif current is not None:
                current.append(line)
        if current is not None:
            raise RuntimeError("plocate batch protocol error: unclosed begin")
        if expected == 0:
            return blocks
        return blocks

    def _first_exact_under_roots(self, plocate_output: str, relative_path: str) -> Optional[str]:
        relative = normalize_relative_path(relative_path)
        if not relative or not plocate_output.strip():
            return None
        roots = self.settings.root_folders
        for line in plocate_output.splitlines():
            absolute = collapse_slashes(line.strip())
            if not absolute:
                continue
            for root in roots:
                expected = collapse_slashes(f"{trim_trailing_slash(root)}/{relative}")
                if absolute == expected:
                    return absolute
        # Fallback: any hit under a configured root ending with the same relative suffix
        for line in plocate_output.splitlines():
            absolute = collapse_slashes(line.strip())
            if not absolute:
                continue
            for root in roots:
                root_n = trim_trailing_slash(root)
                if absolute.startswith(root_n + "/") and absolute.endswith("/" + relative):
                    return absolute
                if absolute == f"{root_n}/{relative}":
                    return absolute
        return None


def resolve_many(
    client: PlocateClient,
    relatives: Sequence[str],
    batch_size: int,
) -> Dict[int, Optional[str]]:
    """Map index -> found absolute path."""
    found: Dict[int, Optional[str]] = {}
    for start in range(0, len(relatives), batch_size):
        chunk = list(relatives[start : start + batch_size])
        # Keep empty relatives as None without calling plocate for them as empty query
        # but batch protocol needs one line per item; empty is ok (script skips plocate).
        results = client.resolve_paths(chunk)
        for offset, path in enumerate(results):
            found[start + offset] = path
        print(
            f"  plocate progress: {min(start + batch_size, len(relatives))}/{len(relatives)}",
            flush=True,
        )
    return found
