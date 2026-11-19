"""
Versión que está corriendo (commit y fecha), para el pie de página.

core/VERSION tiene `export-subst` en .gitattributes: `git archive` (deploy/empaquetar.py) escribe ahí
el commit y su fecha al armar el paquete. En la copia de trabajo de desarrollo el archivo trae aún la
marca sin sustituir y la versión se lee del repositorio.
"""
import subprocess
from functools import lru_cache
from pathlib import Path

_ARCHIVO = Path(__file__).with_name("VERSION")


@lru_cache(maxsize=1)
def actual():
    try:
        texto = _ARCHIVO.read_text(encoding="utf-8").strip()
    except OSError:
        texto = ""
    if texto and "$Format" not in texto:
        return texto
    try:
        raiz = _ARCHIVO.parent.parent
        texto = subprocess.run(
            ["git", "-c", f"safe.directory={raiz.as_posix()}", "log", "-1", "--format=%h %cs"],
            cwd=raiz, capture_output=True, text=True, timeout=5).stdout.strip()
        return f"{texto} (desarrollo)" if texto else "desarrollo"
    except (OSError, subprocess.SubprocessError):
        return "desarrollo"
