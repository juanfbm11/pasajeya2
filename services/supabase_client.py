import base64
import json
import os

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(override=True)  # el .env manda sobre variables del sistema


def _limpiar_url(valor):
    valor = (valor or "").strip().strip('"').strip("'")
    for sufijo in ("/rest/v1/", "/rest/v1", "/"):
        if valor.endswith(sufijo):
            valor = valor[: -len(sufijo)]
    return valor


def _limpiar_clave(valor):
    return (valor or "").strip().strip('"').strip("'")


def _tipo_clave(valor):
    valor = _limpiar_clave(valor)

    if not valor:
        return "vacia"
    if valor.startswith("sb_secret_"):
        return "service_role"
    if valor.startswith("sb_publishable_"):
        return "publishable"

    if valor.count(".") == 2:
        try:
            payload = valor.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            rol = json.loads(base64.urlsafe_b64decode(payload)).get("role")
            return f"legacy_{rol or 'desconocido'}"
        except Exception:
            return "legacy_desconocida"

    return "desconocida"


def _resolver_clave():
    for nombre in (
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_KEY",
    ):
        valor = _limpiar_clave(os.getenv(nombre))
        if valor:
            return valor
    return ""


url = _limpiar_url(os.getenv("SUPABASE_URL"))
key = _resolver_clave()

if not url.startswith("https://") or ".supabase.co" not in url:
    raise RuntimeError("SUPABASE_URL inválida. Debe ser https://TU-PROYECTO.supabase.co")
if not key:
    raise RuntimeError(
        "Falta la clave de Supabase del servidor. Usa SUPABASE_SERVICE_ROLE_KEY o SUPABASE_SECRET_KEY en el .env."
    )
if _tipo_clave(key) == "publishable":
    raise RuntimeError(
        "La clave de Supabase actual es PUBLISHABLE y no puede insertar registros con RLS activo. "
        "Usa la service_role key del proyecto en SUPABASE_SERVICE_ROLE_KEY."
    )
if _tipo_clave(key) not in {"service_role", "legacy_service_role"}:
    raise RuntimeError(
        "La clave de Supabase no es válida para escritura. Debe ser una service_role/secret key del proyecto."
    )

print("Conectando a:", url)  # la URL no es secreta; la clave nunca se imprime

supabase = create_client(url, key)


def nuevo_cliente_auth():
    return create_client(url, key)