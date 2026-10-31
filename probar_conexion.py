"""Ejecuta:  python probar_conexion.py
Muestra qué tipo de clave está usando Flask (sin imprimirla) y prueba las tablas."""
import base64, json
from services.supabase_client import supabase, url, key


def tipo_de_clave(k):
    if k.startswith("sb_secret_"):
        return "SECRET  (correcta para el servidor)"
    if k.startswith("sb_publishable_"):
        return "PUBLISHABLE  (INCORRECTA: es la clave pública, obedece RLS)"
    if k.count(".") == 2:  # JWT antiguo (anon / service_role)
        try:
            payload = k.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            rol = json.loads(base64.urlsafe_b64decode(payload)).get("role")
        except Exception:
            rol = "desconocido"
        if rol == "service_role":
            return "LEGACY service_role  (correcta para el servidor)"
        return f"LEGACY {rol}  (INCORRECTA: usa la service_role / secret)"
    return "formato desconocido"


print("URL :", url)
print("Clave:", tipo_de_clave(key), "| longitud", len(key), "| empieza con", key[:10] + "…")
print()

for tabla in ("terminal", "empresa", "bus", "ruta", "viaje", "pasajero",
              "ticket", "pago", "factura", "administrador"):
    try:
        r = supabase.table(tabla).select("*").limit(1).execute()
        print(f"  OK    {tabla:14} ({len(r.data)} fila(s) de muestra)")
    except Exception as e:
        print(f"  FALLA {tabla:13} -> {str(e)[:150]}")