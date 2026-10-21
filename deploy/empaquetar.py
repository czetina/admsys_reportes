"""
Arma el .zip que se entrega al instalador de producción.

Sale de lo confirmado en git (git archive), así nunca lleva .env, .env.servirsa, .venv, .claude,
pruebas_salida ni __pycache__; además quita lo marcado export-ignore en .gitattributes (README de
desarrollo, .env.example y el SQL del usuario de solo lectura, que va aparte al DBA).

    python deploy/empaquetar.py            -> dist/admsys_reportes_<commit>.zip
"""
import subprocess
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
# Nada de esto debe aparecer dentro del paquete, aunque alguien lo confirme por error
PROHIBIDOS = (".env", ".env.servirsa", ".venv/", ".claude/", "pruebas_salida/", "__pycache__/")


def git(*args):
    return subprocess.run(["git", "-c", f"safe.directory={RAIZ.as_posix()}", *args], cwd=RAIZ,
                          check=True, capture_output=True, text=True).stdout.strip()


def main():
    if git("status", "--porcelain", "--untracked-files=no"):
        sys.exit("Hay cambios sin confirmar: el paquete sale del último commit. Confirmar o descartar primero.")
    commit = git("rev-parse", "--short", "HEAD")
    destino = RAIZ / "dist" / f"admsys_reportes_{commit}.zip"
    destino.parent.mkdir(exist_ok=True)
    git("archive", "--format=zip", "--prefix=admsys_reportes/", "-o", str(destino), "HEAD")

    with zipfile.ZipFile(destino) as z:
        nombres = [n.removeprefix("admsys_reportes/") for n in z.namelist()]
    malos = [n for n in nombres if any(n == p or n.startswith(p) or f"/{p}" in n for p in PROHIBIDOS)]
    if malos:
        destino.unlink()
        sys.exit(f"Paquete cancelado, contiene archivos que no deben salir: {malos}")
    print(f"{destino.relative_to(RAIZ)}  ({sum(1 for n in nombres if not n.endswith('/'))} archivos)")


if __name__ == "__main__":
    main()
