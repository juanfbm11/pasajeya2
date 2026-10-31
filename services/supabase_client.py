from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv(override=True)  # el .env manda sobre variables del sistema


def _limpiar_url(valor):
    valor = (valor or "").strip().strip('"').strip("'")
    for sufijo in ("/rest/v1/", "/rest/v1", "/"):
        if valor.endswith(sufijo):
            valor = valor[: -len(sufijo)]
    return valor


url = _limpiar_url(os.getenv("SUPABASE_URL"))
key = (os.getenv("SUPABASE_KEY") or "").strip().strip('"').strip("'")

if not url.startswith("https://") or ".supabase.co" not in url:
    raise RuntimeError("SUPABASE_URL inválida. Debe ser https://TU-PROYECTO.supabase.co")
if not key:
    raise RuntimeError("Falta SUPABASE_KEY en el .env")

print("Conectando a:", url)  # la URL no es secreta; la clave nunca se imprime

supabase = create_client(url, key)


def nuevo_cliente_auth():
    return create_client(url, key)