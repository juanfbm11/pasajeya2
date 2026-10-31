import os
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from services.supabase_client import supabase, nuevo_cliente_auth


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

if not app.secret_key:
    raise RuntimeError(
        "Falta FLASK_SECRET_KEY en el archivo .env. "
        "Agrega una clave secreta para manejar las sesiones."
    )

TZ = ZoneInfo("America/Bogota")


# ============================================================
# FUNCIONES AUXILIARES Y FILTROS
# ============================================================

def a_local(valor):
    """Convierte una fecha ISO de Supabase a la hora de Colombia."""
    if not valor:
        return None

    fecha = datetime.fromisoformat(valor.replace("Z", "+00:00"))

    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=TZ)

    return fecha.astimezone(TZ)


@app.template_filter("fecha")
def f_fecha(valor):
    return valor.strftime("%d/%m/%Y") if valor else ""


@app.template_filter("hora")
def f_hora(valor):
    return valor.strftime("%I:%M %p") if valor else ""


@app.template_filter("precio")
def f_precio(valor):
    if valor is None:
        return "$0"

    return "$" + f"{float(valor):,.0f}".replace(",", ".")


def vacio_a_none(valor):
    valor = (valor or "").strip()
    return valor or None


# ============================================================
# AYUDAS PARA LA NAVEGACIÓN (REDIRECCIONES)
# ============================================================

def url_busqueda():
    """URL de la última búsqueda del pasajero.
    Sirve para que el botón 'Volver' regrese a los resultados
    en lugar de a un formulario vacío."""
    return url_for("home", **(session.get("busqueda") or {}))


def destino_seguro(url):
    """Solo acepta rutas internas ('/admin'), nunca sitios externos.
    Evita que alguien use ?next=http://sitio-malo.com"""
    if url and url.startswith("/") and not url.startswith("//"):
        return url
    return None


@app.context_processor
def inyectar_ayudas():
    # Permite usar {{ url_busqueda() }} en cualquier plantilla
    return {"url_busqueda": url_busqueda}


@app.errorhandler(404)
def pagina_no_encontrada(error):
    flash("La página que buscas no existe.")
    return redirect(url_for("home"))


# ============================================================
# ORGANIZAR LA INFORMACIÓN DE LOS VIAJES
# ============================================================

def armar_viajes(viajes):

    if not viajes:
        return []

    ids_ruta = list({v["id_ruta"] for v in viajes})
    ids_bus = list({v["id_bus"] for v in viajes})
    ids_viaje = [v["id_viaje"] for v in viajes]

    # Consultar rutas
    rutas_data = (
        supabase.table("ruta")
        .select("*")
        .in_("id_ruta", ids_ruta)
        .execute()
        .data
    )

    rutas = {r["id_ruta"]: r for r in rutas_data}

    # Consultar buses
    buses_data = (
        supabase.table("bus")
        .select("*")
        .in_("id_bus", ids_bus)
        .execute()
        .data
    )

    buses = {b["id_bus"]: b for b in buses_data}

    # Consultar empresas relacionadas con los buses
    ids_empresa = list({
        b["id_empresa"] for b in buses_data
    })

    if ids_empresa:
        empresas_data = (
            supabase.table("empresa")
            .select("*")
            .in_("id_empresa", ids_empresa)
            .execute()
            .data
        )
    else:
        empresas_data = []

    empresas = {
        e["id_empresa"]: e for e in empresas_data
    }

    # Consultar terminales
    terminales_data = (
        supabase.table("terminal")
        .select("*")
        .execute()
        .data
    )

    terminales = {
        t["id_terminal"]: t for t in terminales_data
    }

    # Consultar asientos ocupados
    tickets = (
        supabase.table("ticket")
        .select("id_viaje, asiento")
        .in_("id_viaje", ids_viaje)
        .neq("estado", "cancelado")
        .execute()
        .data
    )

    ocupados = {
        id_viaje: set() for id_viaje in ids_viaje
    }

    for ticket in tickets:
        ocupados[ticket["id_viaje"]].add(
            ticket["asiento"]
        )

    # Completar información de cada viaje
    for viaje in viajes:

        ruta = rutas[viaje["id_ruta"]]
        bus = buses[viaje["id_bus"]]

        salida = a_local(viaje["fecha_salida"])

        viaje["origen"] = terminales[ruta["id_origen"]]
        viaje["destino"] = terminales[ruta["id_destino"]]

        viaje["bus"] = bus
        viaje["empresa"] = empresas[bus["id_empresa"]]

        viaje["salida"] = salida

        duracion = ruta.get("duracion_min")

        viaje["llegada"] = (
            salida + timedelta(minutes=int(duracion))
            if duracion
            else None
        )

        viaje["ocupados"] = ocupados[viaje["id_viaje"]]

        viaje["disponibles"] = (
            bus["capacidad"] - len(viaje["ocupados"])
        )

    return viajes


def obtener_viaje(id_viaje):

    data = (
        supabase.table("viaje")
        .select("*")
        .eq("id_viaje", id_viaje)
        .execute()
        .data
    )

    if not data:
        abort(404)

    return armar_viajes(data)[0]


# ============================================================
# PÁGINA PRINCIPAL Y BÚSQUEDA DE VIAJES
# ============================================================

@app.route("/")
def home():

    terminales = (
        supabase.table("terminal")
        .select("ciudad")
        .execute()
        .data
    )

    ciudades = sorted({
        t["ciudad"]
        for t in terminales
        if t.get("ciudad")
    })

    origen = request.args.get("origen", "")
    destino = request.args.get("destino", "")
    fecha = request.args.get("fecha", "")
    hora = request.args.get("hora", "")

    resultados = None

    if origen and destino and fecha:

        # Guardar la búsqueda para el botón "Volver" de las demás páginas
        session["busqueda"] = {
            clave: valor
            for clave, valor in (
                ("origen", origen),
                ("destino", destino),
                ("fecha", fecha),
                ("hora", hora),
            )
            if valor
        }

        if origen == destino:
            flash("El origen y el destino deben ser diferentes.")
            resultados = []

        else:
            try:
                resultados = buscar_viajes(
                    origen,
                    destino,
                    fecha,
                    hora
                )

            except Exception as error:
                app.logger.exception(
                    "Error al buscar viajes"
                )

                flash(
                    f"No fue posible realizar la búsqueda: {error}"
                )

                resultados = []

    return render_template(
        "home.html",
        ciudades=ciudades,
        resultados=resultados,
        origen=origen,
        destino=destino,
        fecha=fecha,
        hora=hora,
    )


def buscar_viajes(origen, destino, fecha, hora):

    terminales = (
        supabase.table("terminal")
        .select("id_terminal, ciudad")
        .execute()
        .data
    )

    terminales_origen = [
        t["id_terminal"]
        for t in terminales
        if t["ciudad"] == origen
    ]

    terminales_destino = [
        t["id_terminal"]
        for t in terminales
        if t["ciudad"] == destino
    ]

    if not terminales_origen or not terminales_destino:
        return []

    rutas = (
        supabase.table("ruta")
        .select("id_ruta")
        .in_("id_origen", terminales_origen)
        .in_("id_destino", terminales_destino)
        .execute()
        .data
    )

    if not rutas:
        return []

    inicio = datetime.fromisoformat(fecha).replace(
        tzinfo=TZ
    )

    fin = inicio + timedelta(days=1)

    if hora:
        horas, minutos = map(
            int,
            hora.split(":")[:2]
        )

        inicio = inicio.replace(
            hour=horas,
            minute=minutos
        )

    viajes = (
        supabase.table("viaje")
        .select("*")
        .in_(
            "id_ruta",
            [r["id_ruta"] for r in rutas]
        )
        .eq("estado", "programado")
        .gte(
            "fecha_salida",
            inicio.isoformat()
        )
        .lt(
            "fecha_salida",
            fin.isoformat()
        )
        .order("fecha_salida")
        .execute()
        .data
    )

    return armar_viajes(viajes)


# ============================================================
# DETALLE DEL VIAJE
# ============================================================

@app.route("/viaje/<int:id_viaje>")
def info(id_viaje):

    viaje = obtener_viaje(id_viaje)

    return render_template(
        "info.html",
        v=viaje,
        asientos=range(
            1,
            viaje["bus"]["capacidad"] + 1
        ),
    )


# ============================================================
# REGISTRO DEL PASAJERO Y COMPRA DEL TICKET
# ============================================================

@app.route(
    "/pasajero/<int:id_viaje>",
    methods=["GET", "POST"]
)
def pasajero(id_viaje):

    viaje = obtener_viaje(id_viaje)

    asiento = request.values.get(
        "asiento",
        type=int
    )

    if not asiento or not (
        1 <= asiento <= viaje["bus"]["capacidad"]
    ):
        flash("Primero selecciona un asiento válido.")

        return redirect(
            url_for("info", id_viaje=id_viaje)
        )

    if asiento in viaje["ocupados"]:

        flash(
            f"El asiento {asiento} ya está ocupado. Elige otro."
        )

        return redirect(
            url_for("info", id_viaje=id_viaje)
        )

    if request.method == "POST":

        formulario = request.form

        try:

            # 1. Buscar o registrar al pasajero
            datos_pasajero = {
                "nombre_completo": formulario[
                    "txtNombre"
                ].strip(),

                "tipo_documento": formulario[
                    "cmbDocumento"
                ],

                "numero_documento": formulario[
                    "txtDocumento"
                ].strip(),

                "email": vacio_a_none(
                    formulario.get("txtCorreo")
                ),

                "telefono": vacio_a_none(
                    formulario.get("txtTel")
                ),

                "fecha_nacimiento": vacio_a_none(
                    formulario.get("txtFecha")
                ),
            }

            existente = (
                supabase.table("pasajero")
                .select("id_pasajero")
                .eq(
                    "tipo_documento",
                    datos_pasajero["tipo_documento"]
                )
                .eq(
                    "numero_documento",
                    datos_pasajero["numero_documento"]
                )
                .execute()
                .data
            )

            if existente:

                id_pasajero = existente[0]["id_pasajero"]

                (
                    supabase.table("pasajero")
                    .update(datos_pasajero)
                    .eq(
                        "id_pasajero",
                        id_pasajero
                    )
                    .execute()
                )

            else:

                respuesta_pasajero = (
                    supabase.table("pasajero")
                    .insert(datos_pasajero)
                    .execute()
                    .data
                )

                id_pasajero = respuesta_pasajero[0][
                    "id_pasajero"
                ]

            # 2. Crear el ticket
            respuesta_ticket = (
                supabase.table("ticket")
                .insert({
                    "id_viaje": id_viaje,
                    "id_pasajero": id_pasajero,
                    "asiento": asiento,
                })
                .execute()
                .data
            )

            ticket = respuesta_ticket[0]

            # 3. Registrar el pago simulado
            respuesta_pago = (
                supabase.table("pago")
                .insert({
                    "id_ticket": ticket["id_ticket"],
                    "monto": viaje["precio"],
                    "metodo": formulario["cmbMetodo"],
                    "estado": "aprobado",
                    "referencia": (
                        f"SIM-{ticket['codigo_reserva']}"
                    ),
                })
                .execute()
                .data
            )

            pago = respuesta_pago[0]

            # 4. Actualizar el estado del ticket
            (
                supabase.table("ticket")
                .update({"estado": "pagado"})
                .eq(
                    "id_ticket",
                    ticket["id_ticket"]
                )
                .execute()
            )

            # 5. Generar la factura
            supabase.table("factura").insert({
                "id_pago": pago["id_pago"],
                "numero": (
                    f"FAC-{pago['id_pago']:06d}"
                ),
                "subtotal": viaje["precio"],
                "iva": 0,
                "total": viaje["precio"],
            }).execute()

            flash("Compra realizada correctamente.")

            return redirect(
                url_for(
                    "ticket",
                    codigo=ticket["codigo_reserva"],
                    nuevo=1,
                )
            )

        except Exception as error:

            app.logger.exception(
                "Error al completar la compra"
            )

            texto_error = str(error).lower()

            if "duplicate" in texto_error or "23505" in texto_error:

                flash(
                    f"El asiento {asiento} acaba de ser comprado por "
                    "otra persona. Elige otro."
                )

                return redirect(
                    url_for("info", id_viaje=id_viaje)
                )

            flash(
                f"No se pudo completar la compra: {error}"
            )

    return render_template(
        "pasajero.html",
        v=viaje,
        asiento=asiento
    )


# ============================================================
# VISUALIZACIÓN DEL TICKET
# ============================================================

@app.route("/ticket/<codigo>")
def ticket(codigo):

    tickets = (
        supabase.table("ticket")
        .select("*")
        .eq("codigo_reserva", codigo)
        .execute()
        .data
    )

    if not tickets:
        abort(404)

    ticket_data = tickets[0]

    pasajeros = (
        supabase.table("pasajero")
        .select("*")
        .eq(
            "id_pasajero",
            ticket_data["id_pasajero"]
        )
        .execute()
        .data
    )

    if not pasajeros:
        abort(404)

    pagos = (
        supabase.table("pago")
        .select("*")
        .eq(
            "id_ticket",
            ticket_data["id_ticket"]
        )
        .execute()
        .data
    )

    viaje = obtener_viaje(
        ticket_data["id_viaje"]
    )

    return render_template(
        "ticket.html",
        t=ticket_data,
        p=pasajeros[0],
        pago=pagos[0] if pagos else None,
        v=viaje,
        nuevo=request.args.get("nuevo"),
    )


# ============================================================
# PROTECCIÓN DE LAS RUTAS DEL ADMINISTRADOR
# ============================================================

def admin_requerido(funcion):

    @wraps(funcion)
    def interno(*args, **kwargs):

        if not session.get("admin_id"):

            flash(
                "Inicia sesión como administrador para continuar."
            )

            if request.method == "GET":
                siguiente = request.full_path.rstrip("?")
                return redirect(
                    url_for("login", next=siguiente)
                )

            return redirect(
                url_for("login")
            )

        return funcion(*args, **kwargs)

    return interno


# ============================================================
# REGISTRO DEL ADMINISTRADOR
# ============================================================

@app.route("/registro", methods=["GET", "POST"])
def registro():

    if session.get("admin_id"):
        return redirect(url_for("admin"))

    if request.method == "POST":

        nombre = request.form.get(
            "txtNombre",
            ""
        ).strip()

        correo = request.form.get(
            "txtcorreo",
            ""
        ).strip()

        contrasena = request.form.get(
            "txtClave",
            ""
        )

        codigo_invitacion = os.getenv(
            "ADMIN_INVITE_CODE"
        )

        if not nombre or not correo or not contrasena:

            flash(
                "Todos los campos obligatorios deben estar completos."
            )

        elif len(contrasena) < 8:

            flash(
                "La contraseña debe tener al menos 8 caracteres."
            )

        elif (
            codigo_invitacion
            and request.form.get("txtCodigo")
            != codigo_invitacion
        ):

            flash("Código de invitación incorrecto.")

        else:

            try:

                cliente_auth = nuevo_cliente_auth()

                id_admin = None

                # 1) Intentar crear la cuenta en Supabase Auth.
                #    Si ya existía (intento anterior que quedó a medias),
                #    puede lanzar error o devolver un usuario "falso".
                try:
                    creada = cliente_auth.auth.sign_up({
                        "email": correo,
                        "password": contrasena,
                    })

                    if creada.user:
                        id_admin = creada.user.id

                except Exception:
                    app.logger.info(
                        "sign_up falló; se verificará con sign_in"
                    )

                # 2) Verificar con la contraseña y obtener el id REAL.
                #    Si la cuenta ya existía, esto prueba que es de esta
                #    persona. Si es nueva y exige confirmar correo, fallará
                #    y se usa el id del paso 1.
                try:
                    verificada = cliente_auth.auth.sign_in_with_password({
                        "email": correo,
                        "password": contrasena,
                    })

                    if verificada.user:
                        id_admin = verificada.user.id

                except Exception:
                    if id_admin is None:
                        raise Exception(
                            "Ese correo ya está registrado con otra "
                            "contraseña, o falta confirmar el correo."
                        )

                # 3) Crear (o completar) la fila en la tabla administrador.
                #    upsert: si ya existía no da error de duplicado.
                supabase.table("administrador").upsert(
                    {
                        "id_admin": id_admin,
                        "nombre": nombre,
                        "correo": correo,
                    },
                    on_conflict="id_admin",
                ).execute()

                flash(
                    "Cuenta creada correctamente. Ahora inicia sesión. "
                    "Si Supabase solicita confirmar el correo, "
                    "revisa tu bandeja de entrada."
                )

                return redirect(
                    url_for("login")
                )

            except Exception as error:

                app.logger.exception(
                    "Error al registrar administrador"
                )

                flash(
                    f"No se pudo registrar la cuenta: {error}"
                )

    return render_template(
        "registro.html",
        pide_codigo=bool(
            os.getenv("ADMIN_INVITE_CODE")
        ),
    )


# ============================================================
# INICIO DE SESIÓN DEL ADMINISTRADOR
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if session.get("admin_id"):
        return redirect(url_for("admin"))

    if request.method == "POST":

        try:

            respuesta = nuevo_cliente_auth().auth.sign_in_with_password({
                "email": request.form.get(
                    "txtcorreo",
                    ""
                ).strip(),

                "password": request.form.get(
                    "txtClave",
                    ""
                ),
            })

            if not respuesta.user:

                flash(
                    "No fue posible iniciar sesión. Verifica tus datos."
                )

                return render_template("login.html")

            administrador = (
                supabase.table("administrador")
                .select("*")
                .eq(
                    "id_admin",
                    respuesta.user.id
                )
                .execute()
                .data
            )

            if not administrador:

                nuevo_cliente_auth().auth.sign_out()

                flash(
                    "La cuenta existe, pero no está registrada como "
                    "administrador. Entra a «Regístrate» con el mismo "
                    "correo y contraseña para activarla."
                )

            else:

                session.clear()

                session["admin_id"] = respuesta.user.id

                session["admin_nombre"] = (
                    administrador[0]["nombre"]
                )

                flash(
                    "Bienvenido al panel de administración."
                )

                siguiente = destino_seguro(
                    request.values.get("next")
                )

                return redirect(
                    siguiente or url_for("admin")
                )

        except Exception:

            app.logger.exception(
                "Error al iniciar sesión"
            )

            flash(
                "Correo o contraseña incorrectos. Verifica tus datos."
            )

    return render_template("login.html")


# ============================================================
# CERRAR SESIÓN
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    flash("Sesión cerrada correctamente.")

    return redirect(
        url_for("login")
    )


# ============================================================
# PANEL PRINCIPAL DEL ADMINISTRADOR
# ============================================================

@app.route("/admin")
@admin_requerido
def admin():

    empresas = (
        supabase.table("empresa")
        .select("*")
        .order("nombre")
        .execute()
        .data
    )

    terminales = (
        supabase.table("terminal")
        .select("*")
        .order("ciudad")
        .execute()
        .data
    )

    buses = (
        supabase.table("bus")
        .select("*")
        .order("codigo")
        .execute()
        .data
    )

    rutas = (
        supabase.table("ruta")
        .select("*")
        .execute()
        .data
    )

    viajes = (
        supabase.table("viaje")
        .select("*")
        .order(
            "fecha_salida",
            desc=True
        )
        .limit(50)
        .execute()
        .data
    )

    terminales_por_id = {
        t["id_terminal"]: t
        for t in terminales
    }

    for ruta in rutas:

        origen = terminales_por_id.get(
            ruta["id_origen"]
        )

        destino = terminales_por_id.get(
            ruta["id_destino"]
        )

        if origen and destino:

            ruta["label"] = (
                f"{origen['ciudad']} ({origen['nombre']}) → "
                f"{destino['ciudad']} ({destino['nombre']})"
            )

        else:

            ruta["label"] = (
                "Ruta sin terminal asociada"
            )

    return render_template(
        "admin.html",
        empresas=empresas,
        terminales=terminales,
        buses=buses,
        rutas=rutas,
        viajes=armar_viajes(viajes),
    )


# ============================================================
# FUNCIÓN COMÚN PARA INSERTAR REGISTROS
# ============================================================

def insertar(tabla, datos, mensaje_ok):

    try:

        supabase.table(tabla).insert(
            datos
        ).execute()

        flash(mensaje_ok)

    except Exception as error:

        app.logger.exception(
            "Error al insertar en %s",
            tabla
        )

        flash(
            f"No se pudo guardar la información: {error}"
        )

    return redirect(
        url_for("admin")
    )


# ============================================================
# CREAR EMPRESA
# ============================================================

@app.route("/admin/empresa", methods=["POST"])
@admin_requerido
def admin_empresa():

    formulario = request.form

    datos = {
        "nombre": formulario["nombre"].strip(),
        "nit": formulario["nit"].strip(),
    }

    return insertar(
        "empresa",
        datos,
        "Empresa creada correctamente."
    )


# ============================================================
# CREAR TERMINAL
# ============================================================

@app.route("/admin/terminal", methods=["POST"])
@admin_requerido
def admin_terminal():

    formulario = request.form

    datos = {
        "ciudad": formulario["ciudad"].strip(),
        "nombre": formulario["nombre"].strip(),
        "direccion": vacio_a_none(
            formulario.get("direccion")
        ),
    }

    return insertar(
        "terminal",
        datos,
        "Terminal creada correctamente."
    )


# ============================================================
# CREAR BUS
# ============================================================

@app.route("/admin/bus", methods=["POST"])
@admin_requerido
def admin_bus():

    formulario = request.form

    datos = {
        "id_empresa": int(
            formulario["id_empresa"]
        ),

        "codigo": formulario["codigo"].strip(),

        "placa": formulario["placa"].strip().upper(),

        "capacidad": int(
            formulario["capacidad"]
        ),
    }

    return insertar(
        "bus",
        datos,
        "Bus creado correctamente."
    )


# ============================================================
# CREAR RUTA
# ============================================================

@app.route("/admin/ruta", methods=["POST"])
@admin_requerido
def admin_ruta():

    formulario = request.form

    id_origen = formulario["id_origen"]
    id_destino = formulario["id_destino"]

    if id_origen == id_destino:

        flash(
            "El origen y el destino deben ser diferentes."
        )

        return redirect(
            url_for("admin")
        )

    duracion = vacio_a_none(
        formulario.get("duracion_min")
    )

    datos = {
        "id_origen": int(id_origen),
        "id_destino": int(id_destino),
        "duracion_min": (
            int(duracion)
            if duracion
            else None
        ),
    }

    return insertar(
        "ruta",
        datos,
        "Ruta creada correctamente."
    )


# ============================================================
# PROGRAMAR VIAJE
# ============================================================

@app.route("/admin/viaje", methods=["POST"])
@admin_requerido
def admin_viaje():

    formulario = request.form

    try:

        salida = datetime.fromisoformat(
            formulario["fecha_salida"]
        )

        salida = salida.replace(
            tzinfo=TZ
        )

        datos = {
            "id_ruta": int(
                formulario["id_ruta"]
            ),

            "id_bus": int(
                formulario["id_bus"]
            ),

            "fecha_salida": salida.isoformat(),

            "precio": float(
                formulario["precio"]
            ),

            "estado": "programado",
        }

        return insertar(
            "viaje",
            datos,
            "Viaje programado correctamente."
        )

    except (ValueError, TypeError) as error:

        flash(
            f"Verifica los datos del viaje: {error}"
        )

        return redirect(
            url_for("admin")
        )


# ============================================================
# CAMBIAR ESTADO DE UN VIAJE
# ============================================================

@app.route(
    "/admin/viaje/<int:id_viaje>/estado",
    methods=["POST"]
)
@admin_requerido
def admin_viaje_estado(id_viaje):

    estado = request.form.get("estado")

    estados_validos = (
        "programado",
        "en_curso",
        "finalizado",
        "cancelado",
    )

    if estado not in estados_validos:

        flash(
            "El estado seleccionado no es válido."
        )

        return redirect(
            url_for("admin")
        )

    try:

        (
            supabase.table("viaje")
            .update({"estado": estado})
            .eq(
                "id_viaje",
                id_viaje
            )
            .execute()
        )

        flash(
            f"El estado del viaje {id_viaje} cambió a {estado}."
        )

    except Exception as error:

        app.logger.exception(
            "Error al cambiar el estado del viaje"
        )

        flash(
            f"No se pudo actualizar el viaje: {error}"
        )

    return redirect(
        url_for("admin")
    )




if __name__ == "__main__":
    app.run(debug=True)