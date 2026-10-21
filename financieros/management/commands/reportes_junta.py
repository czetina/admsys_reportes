"""
python manage.py reportes_junta --empresa 003 [--anio 2026 --mes 9] [--carpeta "D:/Junta directiva"]

Genera a Excel los reportes de la junta directiva (financieros/junta.py) del cierre indicado
(por defecto, el mes anterior) en <carpeta>/Junta <empresa> <año>-<mes>.
"""
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from financieros import junta
from financieros.contexto import ErrorReporte, empresa, mes_anterior


class Command(BaseCommand):
    help = "Genera a Excel los reportes de la junta directiva en una carpeta local."

    def add_arguments(self, parser):
        anio, mes = mes_anterior(date.today().year, date.today().month)
        parser.add_argument("--empresa", required=True, help="Código de empresa, p. ej. 003")
        parser.add_argument("--anio", type=int, default=anio, help=f"Año del cierre (por defecto {anio})")
        parser.add_argument("--mes", type=int, default=mes, choices=range(1, 13), help=f"Mes del cierre (por defecto {mes})")
        parser.add_argument("--carpeta", default=str(Path.home() / "Documents" / "Junta directiva"),
                            help="Carpeta destino; dentro se crea una subcarpeta por empresa y cierre")
        parser.add_argument("--usuario", default="", help="Usuario de ADMSys (solo para filtros que dependen de él)")

    def handle(self, *args, **o):
        try:
            emp = empresa(o["empresa"])
        except ErrorReporte as e:
            raise CommandError(str(e))
        destino = Path(o["carpeta"]) / f"Junta {emp.codempresa} {o['anio']}-{o['mes']:02d}"
        self.stdout.write(f"{emp.razonsocial} - cierre {o['mes']:02d}/{o['anio']} en {destino}")
        fallos = 0
        resultados = junta.genera(emp.codempresa, o["usuario"], o["anio"], o["mes"])
        junta.guarda(resultados, destino)
        for r in resultados:
            if r.contenido:
                self.stdout.write(self.style.SUCCESS(f"  OK  {r.nombre}: {r.archivo}"))
            else:
                fallos += 1
                self.stdout.write(self.style.ERROR(f"  ERR {r.nombre}: {r.error}"))
        if fallos:
            raise CommandError(f"{fallos} reporte(s) no se generaron.")
