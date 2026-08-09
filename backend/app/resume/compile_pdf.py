from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_COMPILE_TIMEOUT_SECONDS = 30


class LatexCompileError(RuntimeError):
    """Raised when Tectonic fails to produce a PDF from the given source."""


async def compile_latex_to_pdf(tex_source: str) -> bytes:
    """Compile a LaTeX source string to PDF bytes via Tectonic (a
    self-contained LaTeX engine — see backend/Dockerfile; no system TeX Live
    install required). Runs in a request-scoped temp directory that's always
    cleaned up, so nothing from a tailoring request survives past this call
    (see spec: Resume tailoring — nothing is persisted)."""
    with tempfile.TemporaryDirectory(prefix="resume-tailor-") as tmpdir:
        tex_path = Path(tmpdir) / "resume.tex"
        tex_path.write_text(tex_source, encoding="utf-8")

        try:
            process = await asyncio.create_subprocess_exec(
                "tectonic",
                "--outdir",
                tmpdir,
                str(tex_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=_COMPILE_TIMEOUT_SECONDS
            )
        except FileNotFoundError as exc:
            raise LatexCompileError("tectonic is not installed on this host") from exc
        except TimeoutError as exc:
            process.kill()
            raise LatexCompileError("LaTeX compilation timed out") from exc

        if process.returncode != 0:
            detail = stderr.decode(errors="replace") or stdout.decode(errors="replace")
            logger.warning("tectonic compile failed: %s", detail[:2000])
            raise LatexCompileError(detail[:2000])

        pdf_path = Path(tmpdir) / "resume.pdf"
        if not pdf_path.exists():
            raise LatexCompileError("tectonic reported success but produced no PDF")
        return pdf_path.read_bytes()
