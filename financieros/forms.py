from datetime import date

from django import forms

from core.db import consulta, consulta_uno

from . import contexto as ctx
from .contexto import mes_anterior
from .registro import ETIQUETAS

MESES = [(1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"), (5, "Mayo"), (6, "Junio"),
         (7, "Julio"), (8, "Agosto"), (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"), (12, "Diciembre")]


class JuntaForm(forms.Form):
    """Paquete de la junta directiva: solo el cierre; cada reporte usa sus valores iniciales."""
    anio = forms.IntegerField(label="Año", min_value=1990, max_value=2100,
                              widget=forms.NumberInput(attrs={"class": "form-control"}))
    mes = forms.TypedChoiceField(label="Mes", choices=MESES, coerce=int,
                                 widget=forms.Select(attrs={"class": "form-select"}))
    salida = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hoy = date.today()
        self.fields["anio"].initial, self.fields["mes"].initial = mes_anterior(hoy.year, hoy.month)


class ParametrosForm(forms.Form):
    nombre = forms.CharField(label="Nombre del reporte", max_length=120,
                             widget=forms.TextInput(attrs={"class": "form-control"}))
    anio = forms.IntegerField(label="Año", min_value=1990, max_value=2100,
                              widget=forms.NumberInput(attrs={"class": "form-control"}))
    mes = forms.TypedChoiceField(label="Mes", choices=MESES, coerce=int,
                                 widget=forms.Select(attrs={"class": "form-select"}))
    nivel = forms.IntegerField(label="Nivel", min_value=1, max_value=20, required=False,
                               help_text="Vacío = todos los niveles",
                               widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Todos"}))
    salida = forms.ChoiceField(label="Salida", choices=[("excel", "Excel"), ("pantalla", "Pantalla")],
                               widget=forms.RadioSelect)

    def __init__(self, *args, definicion, codempresa=None, coduser="", **kwargs):
        super().__init__(*args, **kwargs)
        self.definicion = definicion
        self.coduser = coduser
        if not definicion.usa_nivel:
            del self.fields["nivel"]
        self._agrega_campos_extra(definicion.campos, codempresa)
        if "nivel" in self.fields and definicion.nivel_inicial:
            self.fields["nivel"].initial = definicion.nivel_inicial
        if "meses" in self.fields:
            self.fields["meses"].initial = definicion.meses_inicial
        hoy = date.today()
        self.fields["nombre"].initial = definicion.titulo
        self.fields["anio"].initial = hoy.year
        self.fields["mes"].initial = hoy.month
        self.fields["salida"].initial = definicion.salida
        if getattr(definicion, "sin_fechas", False):     # p. ej. ficha de responsabilidad: sin periodo ni fechas
            del self.fields["anio"], self.fields["mes"]
        elif not definicion.usa_periodo:          # cuentas corrientes: fechas en lugar de año / mes
            del self.fields["anio"], self.fields["mes"]
            fecha = {"class": "form-control", "type": "date"}
            opcional = getattr(definicion, "fechas_opcionales", False)
            de = f" {definicion.etiqueta_fecha}" if getattr(definicion, "etiqueta_fecha", "") else ""
            if definicion.usa_fecha_inicial:
                self.fields["fecha_ini"] = forms.DateField(
                    label=f"Fecha{de} inicial" if opcional else "Fecha inicio", required=not opcional,
                    initial=None if opcional else hoy.replace(day=1),
                    help_text="En blanco = sin límite" if opcional else "",
                    widget=forms.DateInput(fecha, format="%Y-%m-%d"))
            self.fields["fecha_fin"] = forms.DateField(
                label=(f"Fecha{de} final" if opcional else "Fecha fin") if definicion.usa_fecha_inicial else
                ("Al" if opcional else "Saldos al"),
                required=not opcional, initial=None if opcional and definicion.usa_fecha_inicial else hoy,
                help_text="En blanco = sin límite" if opcional and definicion.usa_fecha_inicial else "",
                widget=forms.DateInput(fecha, format="%Y-%m-%d"))

        if definicion.periodo2:
            if definicion.periodo2 == "mes_anterior":
                a2, m2 = mes_anterior(hoy.year, hoy.month)
            else:
                a2, m2 = hoy.year - 1, hoy.month
            self.fields["anio2"] = forms.IntegerField(
                label="Año", min_value=1990, max_value=2100, initial=a2,
                widget=forms.NumberInput(attrs={"class": "form-control"}))
            self.fields["mes2"] = forms.TypedChoiceField(
                label="Mes", choices=MESES, coerce=int, initial=m2,
                widget=forms.Select(attrs={"class": "form-select"}))

        for nombre, defecto in definicion.opciones.items():
            self.fields[nombre] = forms.BooleanField(
                label=ETIQUETAS[nombre], required=False, initial=defecto,
                widget=forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"}))

    def _agrega_campos_extra(self, campos, codempresa):
        if not campos:
            return
        dim = getattr(self.definicion, "dimension", "cc")
        centros = ctx.centros(codempresa, dim=dim) if {"centro", "centro_rango", "centros"} & set(campos) else {}
        nombre_dim = ctx.rotula("Centro de costo", dim)
        opciones = [("", "— Todos —")] + [(c.codigo, f"{c.codigo} · {c.nombre}") for c in centros.values()]
        select = {"class": "form-select"}
        if "centro" in campos:
            self.fields["centro"] = forms.ChoiceField(label=nombre_dim, choices=opciones, required=False,
                                                      widget=forms.Select(attrs=select))
        if "centro_rango" in campos:
            self.fields["cc_desde"] = forms.ChoiceField(label=f"{nombre_dim} inicio", choices=opciones,
                                                        required=False, widget=forms.Select(attrs=select))
            self.fields["cc_hasta"] = forms.ChoiceField(label=f"{nombre_dim} fin", choices=opciones,
                                                        required=False, widget=forms.Select(attrs=select))
        if "centros" in campos:
            self.fields["centros"] = forms.MultipleChoiceField(
                label=ctx.rotula("Centros de costo", dim), required=False,
                choices=[(c.codigo, f"{c.codigo} · {c.nombre}") for c in centros.values() if c.ultimo],
                help_text="Sin selección = todos los centros",
                widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}))
        if "cuenta_rango" in campos:
            texto = {"class": "form-control", "placeholder": "Código de cuenta", "data-buscar": "cuentas"}
            self.fields["cuenta_desde"] = forms.CharField(label="Cuenta inicio", required=False, max_length=24,
                                                          widget=forms.TextInput(attrs=texto))
            self.fields["cuenta_hasta"] = forms.CharField(label="Cuenta fin", required=False, max_length=24,
                                                          widget=forms.TextInput(attrs=texto))
        if "directorio_rango" in campos:
            texto = {"class": "form-control", "placeholder": "Código de directorio", "data-buscar": "directorios"}
            self.fields["directorio_desde"] = forms.CharField(label="Directorio de", required=False, max_length=24,
                                                              widget=forms.TextInput(attrs=texto))
            self.fields["directorio_hasta"] = forms.CharField(label="Directorio a", required=False, max_length=24,
                                                              widget=forms.TextInput(attrs=texto))
        if "meses" in campos:
            self.fields["meses"] = forms.IntegerField(label="Meses a reportar", min_value=1, max_value=12, initial=12,
                                                      widget=forms.NumberInput(attrs={"class": "form-control"}))
        if "periodo_inicial" in campos:
            self.fields["anio_ini"] = forms.IntegerField(
                label="Desde año", min_value=1990, max_value=2100, required=False,
                help_text="Opcional: reemplaza «Meses a reportar»",
                widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "—"}))
            self.fields["mes_ini"] = forms.TypedChoiceField(
                label="Desde mes", choices=[("", "—")] + MESES, coerce=int, empty_value=None, required=False,
                widget=forms.Select(attrs=select))
        if "orden" in campos:
            self.fields["orden"] = forms.TypedChoiceField(
                label="Ordenar por", coerce=int, initial=1,
                choices=[(1, "Fecha"), (2, "Código de directorio"), (3, "Nombre de directorio"),
                         (4, "Tipo, serie y número de documento")],
                widget=forms.Select(attrs=select))
        if "presentacion" in campos:
            from .libros import PRESENTACIONES
            self.fields["presentacion"] = forms.TypedChoiceField(
                label="Presentación", coerce=int, initial=4, choices=sorted(PRESENTACIONES.items()),
                widget=forms.Select(attrs=select))
        if {"clasificaciones", "moneda"} & set(campos):
            codgrupo = (consulta_uno("SELECT codgrupo FROM empresas WHERE codempresa = %s", (codempresa,)) or {}).get("codgrupo", "")
        if "clasificaciones" in campos:
            filas = consulta("SELECT DISTINCT codclasifica, tipo FROM gruclasificaciones WHERE codgrupo = %s "
                             "ORDER BY tipo, codclasifica", (codgrupo,))
            self.fields["clasificaciones"] = forms.MultipleChoiceField(
                label="Clasificaciones", required=False,
                choices=[(f["codclasifica"], f"{f['codclasifica']} · {f['tipo'].title()}" if f["tipo"] else f["codclasifica"])
                         for f in filas],
                help_text="Sin selección = todas las clasificaciones",
                widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}))
        if "moneda" in campos:
            monedas = consulta("SELECT codmoneda, nommoneda FROM commonedas ORDER BY codmoneda")
            self.fields["codmoneda"] = forms.ChoiceField(
                label="Moneda", required=False, widget=forms.Select(attrs=select),
                choices=[("", "— Todas —")] + [(m["codmoneda"], f"{m['codmoneda']} · {m['nommoneda']}") for m in monedas])
        texto = {"class": "form-control"}
        if "cliente_base" in campos:
            self.fields["codaca"] = forms.CharField(label="Cliente base", required=False, max_length=24,
                                                    widget=forms.TextInput(attrs={**texto, "placeholder": "Código", "data-buscar": "directorios"}))
        if "cuenta_aux" in campos:
            self.fields["cuenta_aux"] = forms.CharField(label="Cuenta de provisión", required=False, max_length=24,
                                                        widget=forms.TextInput(attrs={**texto, "placeholder": "Código de cuenta", "data-buscar": "cuentas"}))
        if "tipo_doc" in campos:
            self.fields["codtipo"] = forms.CharField(label="Tipo de documento", required=False, max_length=10,
                                                     widget=forms.TextInput(attrs={**texto, "placeholder": "Todos"}))
        if "docref" in campos:
            self.fields["docref"] = forms.CharField(label="Documento de referencia", required=False, max_length=40,
                                                    widget=forms.TextInput(attrs=texto))
        if "estado_doc" in campos:
            from .reportes_ctacte import ESTADOS_DOC
            self.fields["estado_doc"] = forms.ChoiceField(
                label="Mostrar registros", initial="todos", widget=forms.Select(attrs=select),
                choices=[(k, v[0]) for k, v in ESTADOS_DOC.items()])
        if "contrato" in campos:
            self.fields["no_contrato"] = forms.IntegerField(label="# Contrato", required=False, min_value=0,
                                                            widget=forms.NumberInput(attrs={**texto, "placeholder": "—"}))
        if "tipo_saldos" in campos:
            self.fields["tipo_saldos"] = forms.TypedChoiceField(
                label="Saldos", coerce=int, initial=1, widget=forms.Select(attrs=select),
                choices=[(1, "Deudores y acreedores"), (2, "Solo deudores"), (3, "Solo acreedores")])
        if "fecha_base" in campos:
            self.fields["fecha_base"] = forms.TypedChoiceField(
                label="Utilizar fecha", coerce=int, initial=1, widget=forms.Select(attrs=select),
                choices=[(1, "Movimiento"), (2, "Vencimiento")])
        if "orden_cc" in campos:
            self.fields["orden_cc"] = forms.TypedChoiceField(
                label="Ordenar registros por", coerce=int, initial=1, widget=forms.Select(attrs=select),
                choices=[(1, "Nombre"), (2, "Código")])
        if "orden_pendientes" in campos:
            self.fields["orden_cc"] = forms.TypedChoiceField(
                label="Agrupados por", coerce=int, initial=1, widget=forms.Select(attrs=select),
                choices=[(1, "Nombre del directorio"), (2, "Mes de vencimiento")])
        if "antiguedad" in campos:
            # proyección de pagos: VFP propone semanas (5 periodos de 7 días); antigüedad de saldos, 30 días
            self.fields["intervalo"] = forms.IntegerField(label="Intervalo de días", min_value=1, max_value=365,
                                                          initial=7 if "semanal" in campos else 30,
                                                          widget=forms.NumberInput(attrs=texto))
            self.fields["columnas_antiguedad"] = forms.IntegerField(
                label="Columnas de antigüedad", min_value=1, max_value=30, initial=5,
                widget=forms.NumberInput(attrs=texto))
        for campo, uso in (("sucursal_compras", "usa_compras"), ("sucursal_ventas", "usa_ventas")):
            if campo in campos:      # como en VFP: solo si la empresa tiene sucursales de IVA para ese libro
                filas = consulta(f"SELECT sucursal, nombre FROM iva_sucursales WHERE codempresa = %s AND {uso} = 1 "
                                 "ORDER BY sucursal", (codempresa,))
                if filas:
                    opciones_suc = [("", "— Todas —")]
                    if campo == "sucursal_ventas":
                        opciones_suc.append((0, "0 · Documentos sin sucursal"))
                    opciones_suc += [(f["sucursal"], f"{f['sucursal']} · {f['nombre']}") for f in filas]
                    self.fields["sucursal"] = forms.TypedChoiceField(
                        label="Sucursal", choices=opciones_suc, coerce=int, empty_value=None, required=False,
                        widget=forms.Select(attrs=select))
        if "orden_ventas" in campos:
            self.fields["orden"] = forms.TypedChoiceField(
                label="Ordenar por", coerce=int, initial=1, choices=[(1, "Fecha"), (2, "Documento")],
                widget=forms.Select(attrs=select))
        if "referencia" in campos:
            texto_ref = {"class": "form-control"}
            self.fields["codsistema_ref"] = forms.CharField(
                label="Sistema del documento que aplica", required=False, max_length=3,
                widget=forms.TextInput(attrs={**texto_ref, "placeholder": "CHQ, CXP… (vacío = todos)"}))
            self.fields["codctachq"] = forms.CharField(
                label="Cuenta de cheques", required=False, max_length=24,
                widget=forms.TextInput(attrs={**texto_ref, "placeholder": "Solo para CHQ"}))
            self.fields["tipos_ref"] = forms.CharField(
                label="Tipos de documento", required=False, max_length=200,
                help_text="Separados por coma; vacío = todos",
                widget=forms.TextInput(attrs={**texto_ref, "placeholder": "CH, TR…"}))
        if "tipo_rango" in campos:
            texto_t = {"class": "form-control", "placeholder": "Código de tipo"}
            self.fields["tipo_desde"] = forms.CharField(label="Tipo doc. inicio", required=False, max_length=10,
                                                        widget=forms.TextInput(attrs=texto_t))
            self.fields["tipo_hasta"] = forms.CharField(label="Tipo doc. fin", required=False, max_length=10,
                                                        widget=forms.TextInput(attrs=texto_t))
        if "estado_fac" in campos:
            self.fields["estado_fac"] = forms.ChoiceField(
                label="Facturas", initial="todas", widget=forms.Select(attrs=select),
                choices=[("todas", "Todas las facturas"), ("pagadas", "Pagadas"), ("pendientes", "Pendientes de pago")])
        if "orden_fac" in campos:
            self.fields["orden_fac"] = forms.TypedChoiceField(
                label="Ordenar por", coerce=int, initial=1, widget=forms.Select(attrs=select),
                choices=[(1, "Tipo, serie y número"), (2, "Fecha ascendente"), (3, "Fecha descendente")])
        if "orden_consec" in campos:
            self.fields["orden_fac"] = forms.TypedChoiceField(
                label="Ordenar por", coerce=int, initial=1, widget=forms.Select(attrs=select),
                choices=[(1, "Documento"), (2, "Consecutivo")])
        if "conceptos_cc" in campos:
            for nombre, etiqueta in (("codcc", "Centro de costo (CC)"), ("codci", "Centro de integración (CI)"),
                                     ("codca", "Centro de actividad (CA)")):
                self.fields[nombre] = forms.CharField(label=etiqueta, required=False, max_length=24,
                                                      widget=forms.TextInput(attrs={"class": "form-control",
                                                                                    "placeholder": "Todos"}))
        if {"cuenta_chq", "cuenta_chq_opc"} & set(campos):
            from .reportes_chq import cuentas_permitidas
            ctas = cuentas_permitidas(codempresa, self.coduser, self.definicion.tipos_cuenta)
            opciones_c = [(c["codctachq"].strip(), f"{c['codctachq'].strip()} · {c['nomctachq'].strip()} "
                                                   f"({c['codmoneda'].strip()})") for c in ctas]
            obligatoria = "cuenta_chq" in campos
            self.fields["codctachq"] = forms.ChoiceField(
                label="Cuenta" if obligatoria else "Cuenta (opcional)", required=obligatoria,
                choices=([] if obligatoria else [("", "— Todas —")]) + opciones_c, widget=forms.Select(attrs=select))
            if obligatoria and not opciones_c:
                self.fields["codctachq"].error_messages["required"] = (
                    "No hay cuentas de este tipo en la empresa (o su usuario no tiene acceso a ninguna).")
        if "tipo_cuenta" in campos:
            tipos = consulta("SELECT codtipocta, nomtipocta FROM ownchqtipocta ORDER BY codtipocta")
            self.fields["codtipocta"] = forms.ChoiceField(
                label="Tipo de cuenta", initial="DM", widget=forms.Select(attrs=select),
                choices=[(t["codtipocta"].strip(), f"{t['codtipocta'].strip()} · {t['nomtipocta'].strip()}") for t in tipos])
        for campo, etiquetas in (("estado_aut", ("Autorizados", "Pendientes de autorizar")),
                                 ("estado_ent", ("Entregados", "Pendientes de entrega"))):
            if campo in campos:
                self.fields["estado_chq"] = forms.ChoiceField(
                    label="Mostrar", initial="si", widget=forms.Select(attrs=select),
                    choices=[("si", etiquetas[0]), ("pendientes", etiquetas[1])])
        if "usuario_chq" in campos:
            usuarios = consulta("SELECT coduser, nombre FROM vfusers WHERE codempresa = %s ORDER BY nombre", (codempresa,))
            self.fields["usuario_chq"] = forms.ChoiceField(
                label="Usuario", required=False, widget=forms.Select(attrs=select),
                choices=[("", "— Todos —")] + [(u["coduser"].strip(), u["nombre"].strip() or u["coduser"].strip())
                                               for u in usuarios])
        if {"tasa", "tasa_sola"} & set(campos):
            self.fields["tasa_cambio"] = forms.DecimalField(
                label="Tasa de cambio", required=False, max_digits=16, decimal_places=8, min_value=0,
                help_text="Vacío = la de comtdc más cercana a la fecha",
                widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.00001", "placeholder": "Automática"}))
        if "tasa" in campos:
            self.fields["meses_caducado"] = forms.IntegerField(
                label="Meses para tomar como caducado", min_value=0, max_value=60, initial=6,
                widget=forms.NumberInput(attrs={"class": "form-control"}))
        if "numero_rango" in campos:
            for nombre, etiqueta in (("numero_desde", "Del número"), ("numero_hasta", "Al número")):
                self.fields[nombre] = forms.IntegerField(label=etiqueta, required=False, min_value=0,
                                                         widget=forms.NumberInput(attrs={"class": "form-control"}))
        if "proyeccion" in campos:
            from .reportes_chq import DIAS_SEMANA
            self.fields["proyectar_por"] = forms.ChoiceField(
                label="Proyectar por", initial="mes", widget=forms.Select(attrs=select),
                choices=[("semana", "Semana"), ("mes", "Mes"), ("anio", "Año")])
            self.fields["primer_dia"] = forms.TypedChoiceField(
                label="Primer día de la semana", coerce=int, initial=0, widget=forms.Select(attrs=select),
                choices=list(enumerate(d.capitalize() for d in DIAS_SEMANA)))
            self.fields["mostrar_por"] = forms.ChoiceField(
                label="Mostrar por", initial="directorio", widget=forms.Select(attrs=select),
                choices=[("directorio", "Directorio"), ("clasificacion", "Clasificación"), ("aca", "Código acaparador")])
        # Activos fijos: «filtros_activos» = todos los rangos + orden de la depreciación; o rangos sueltos
        rangos_af = (("activo", "Activo"), ("ubica", "Ubicación"), ("tipoact", "Tipo de activo"),
                     ("depto", "Departamento"), ("responsable", "Responsable"))
        for clave, etiqueta in rangos_af:
            if "filtros_activos" in campos or f"af_{clave}" in campos:
                for lado, sufijo in (("desde", "inicial"), ("hasta", "final")):
                    self.fields[f"{clave}_{lado}"] = forms.CharField(
                        label=f"{etiqueta} {sufijo}", required=False, max_length=30,
                        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Todos"}))
        ordenes_af = {"filtros_activos": [(1, "Departamento, tipo de activo"), (2, "Tipo de activo, departamento")],
                      "orden_depto_tipo": [(1, "Departamento, tipo de activo"), (2, "Tipo de activo, departamento")],
                      "orden_ficha": [(1, "Código"), (2, "Nombre")],
                      "agrupa_tipos": [(1, "Tipo de activo"), (2, "Tipo de activo y responsable")]}
        for marca, opciones_o in ordenes_af.items():
            if marca in campos:
                self.fields["orden_activos"] = forms.TypedChoiceField(
                    label="Ordenar por", coerce=int, initial=1, widget=forms.Select(attrs=select), choices=opciones_o)
        if {"responsable_af", "responsable_af_opc"} & set(campos):
            resp = consulta("SELECT codresponsable, nombre FROM acf_responsables WHERE codempresa = %s "
                            "AND (fecha_baja IS NULL OR fecha_baja = '0000-00-00') ORDER BY nombre", (codempresa,))
            obligatorio = "responsable_af" in campos
            self.fields["codresponsable"] = forms.ChoiceField(
                label="Responsable", required=obligatorio, widget=forms.Select(attrs=select),
                choices=([] if obligatorio else [("", "— Todos —")]) +
                [(r["codresponsable"].strip(), f"{r['nombre'].strip()} ({r['codresponsable'].strip()})") for r in resp])
        if "orden_tipo_serie" in campos:
            self.fields["orden_fac"] = forms.TypedChoiceField(
                label="Ordenar por", coerce=int, initial=1, widget=forms.Select(attrs=select),
                choices=[(1, "Tipo de documento"), (2, "Serie")])
        if "serie" in campos:
            self.fields["serie"] = forms.CharField(label="Serie", required=False, max_length=20,
                                                   widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Todas"}))
        if "serie_rango" in campos:
            for nombre, etiqueta in (("serie_desde", "Serie inicio"), ("serie_hasta", "Serie fin")):
                self.fields[nombre] = forms.CharField(label=etiqueta, required=False, max_length=20,
                                                      widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Todas"}))
        if "estado_reg" in campos:
            from .reportes_chq import REGISTROS
            self.fields["estado_reg"] = forms.ChoiceField(
                label="Mostrar registros", initial="todos", widget=forms.Select(attrs=select),
                choices=[(k, v[0]) for k, v in REGISTROS.items()])
        if "valor_doc" in campos:
            self.fields["valor_doc"] = forms.DecimalField(
                label="Valor del documento", required=False, max_digits=16, decimal_places=2, min_value=0,
                widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Todos"}))
        if "resolucion" in campos:
            self.fields["resolucion"] = forms.CharField(
                label="Resolución", required=False, max_length=60,
                help_text="Número de resolución de la SAT que autoriza el libro (sale en el encabezado)",
                widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Opcional"}))
        if "folio" in campos:
            self.fields["folio"] = forms.IntegerField(
                label="Folio inicial", min_value=0, max_value=999999, initial=0, required=False,
                help_text="0 = sin folio", widget=forms.NumberInput(attrs={"class": "form-control"}))

    def clean(self):
        datos = super().clean()
        if "anio_ini" in self.fields and bool(datos.get("anio_ini")) != bool(datos.get("mes_ini")):
            raise forms.ValidationError("Indique año y mes del periodo inicial, o deje ambos vacíos.")
        return datos

    @property
    def titulo_filtros(self):
        if {"centro", "cc_desde", "centros"} & set(self.fields):
            return ctx.rotula("Centros de costo y cuentas", getattr(self.definicion, "dimension", "cc"))
        return "Filtros"

    @property
    def campos_centro(self):
        nombres = ("centro", "cc_desde", "cc_hasta", "cuenta_desde", "cuenta_hasta", "directorio_desde",
                   "directorio_hasta", "meses", "anio_ini", "mes_ini", "orden", "presentacion", "folio",
                   "tipo_saldos", "fecha_base", "orden_cc", "intervalo", "columnas_antiguedad", "codmoneda",
                   "codaca", "cuenta_aux", "no_contrato", "codtipo", "docref", "estado_doc", "sucursal",
                   "codsistema_ref", "codctachq", "tipos_ref", "tipo_desde", "tipo_hasta", "estado_fac",
                   "orden_fac", "codcc", "codci", "codca", "codtipocta", "estado_chq", "usuario_chq",
                   "numero_desde", "numero_hasta", "tasa_cambio", "meses_caducado", "serie", "serie_desde",
                   "serie_hasta", "estado_reg", "valor_doc", "proyectar_por", "primer_dia", "mostrar_por", "resolucion",
                   "activo_desde", "activo_hasta", "ubica_desde", "ubica_hasta", "tipoact_desde", "tipoact_hasta",
                   "depto_desde", "depto_hasta", "responsable_desde", "responsable_hasta", "orden_activos",
                   "codresponsable")
        return [self[n] for n in nombres if n in self.fields]

    @property
    def campos_lista(self):
        """Listas de selección múltiple con filtro (centros de costo, clasificaciones)."""
        return [self[n] for n in ("centros", "clasificaciones") if n in self.fields]

    @property
    def campos_opciones(self):
        return [self[n] for n in self.definicion.opciones]

    def primer_error(self):
        for errores in self.errors.values():
            if errores:
                return errores[0]
        return "Revise los parámetros."
