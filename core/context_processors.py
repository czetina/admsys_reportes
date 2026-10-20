from django.conf import settings

# ambiente -> (etiqueta, clase CSS, abreviatura para el título de la pestaña)
AMBIENTES = {
    "desarrollo": ("Desarrollo", "ambiente-desarrollo", "DES"),
    "pruebas": ("Pruebas", "ambiente-pruebas", "PRU"),
    "produccion": ("Producción", "ambiente-produccion", "PROD"),
}


def sesion(request):
    return {"usuario": getattr(request, "usuario", None), "ambiente": _ambiente()}


def _ambiente():
    """Datos de la franja de ambiente (ADMSYS_AMBIENTE); None si no está activada."""
    clave = settings.ADMSYS_AMBIENTE
    if not clave:
        return None
    etiqueta, clase, corto = AMBIENTES.get(clave, (clave.title(), "ambiente-pruebas", clave[:4].upper()))
    cfg = settings.VADMON
    return {"etiqueta": etiqueta, "clase": clase, "corto": corto,
            "servidor": f"{cfg['host']}:{cfg['port']}", "base": cfg["database"]}
