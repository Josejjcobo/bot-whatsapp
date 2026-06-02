import os
import time
import threading
import unicodedata
import requests
from flask import Flask, request, send_from_directory

app = Flask(__name__)

# --- CONFIGURACIÓN DE VARIABLES ---
ACCESS_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
CC_API_KEY = os.environ.get("CLOUD_CONVERT_API_KEY")
BASE_URL = os.environ.get("BASE_URL", "https://bot-whatsapp-zcek.onrender.com")

# --- RUTAS ---
UPLOAD_FOLDER = '/tmp/archivos_bot'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- ESTADO POR USUARIO ---
sesiones = {}

# --- CONVERSIONES SOPORTADAS ---
CONVERSIONES = {
    "docx": [
        ("docx", "pdf",  ".pdf",  "PDF",  "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ],
    "xlsx": [
        ("xlsx", "pdf",  ".pdf",  "PDF",  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ],
    "pdf": [
        ("pdf",  "docx", ".docx", "Word (DOCX)", "application/pdf"),
    ],
    "jpg": [
        ("jpg",  "pdf",  ".pdf",  "PDF",  "image/jpeg"),
    ],
    "jpeg": [
        ("jpeg", "pdf",  ".pdf",  "PDF",  "image/jpeg"),
    ],
    "png": [
        ("png",  "pdf",  ".pdf",  "PDF",  "image/png"),
    ],
}

MENSAJE_BIENVENIDA = (
    "🤖 *¡Hola! Soy PDFMagic Bot*\n\n"
    "Puedo convertir los siguientes archivos:\n\n"
    "📄 *DOCX* → PDF\n"
    "📊 *XLSX* → PDF\n"
    "🖼️ *JPG / PNG* → PDF\n"
    "📑 *PDF* → Word (DOCX)\n\n"
    "Envíame un archivo y te diré qué puedo hacer con él."
)

# ─────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────

def sanitizar_nombre(nombre):
    """Elimina acentos y reemplaza espacios por guiones bajos."""
    n = unicodedata.normalize('NFKD', nombre)
    n = n.encode('ascii', 'ignore').decode('ascii')
    n = n.replace(' ', '_')
    return n

def programar_borrado(ruta):
    """Espera 10 minutos y elimina el archivo."""
    time.sleep(600)
    if os.path.exists(ruta):
        os.remove(ruta)
        print(f"🧹 Limpieza: {ruta} eliminado.")

def enviar_mensaje_texto(receptor, texto):
    """Envía un mensaje de texto por WhatsApp."""
    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": receptor,
        "type": "text",
        "text": {"body": texto}
    }
    try:
        r = requests.post(url, headers=headers, json=payload)
        print(f"📤 Mensaje a {receptor}: {r.status_code}")
        if r.status_code != 200:
            print(f"  Error: {r.text}")
    except Exception as e:
        print(f"❌ Error enviando mensaje: {e}")

# ─────────────────────────────────────────
# LÓGICA DE CONVERSIÓN
# ─────────────────────────────────────────

def procesar_y_convertir(file_url, nombre_original, input_format, output_format, ext_salida, mime_type, telefono):
    """Descarga, convierte con CloudConvert y entrega el link al usuario."""
    try:
        nombre_seguro = sanitizar_nombre(nombre_original)
        print(f"📝 {nombre_original} → {nombre_seguro}")

        # 1. Descargar archivo desde Meta
        r = requests.get(file_url, headers={"Authorization": f"Bearer {ACCESS_TOKEN}"})
        input_path = os.path.join(UPLOAD_FOLDER, nombre_seguro)
        with open(input_path, 'wb') as f:
            f.write(r.content)
        print(f"📥 Descargado: {nombre_seguro}")

        # 2. Crear job en CloudConvert
        cc_headers = {
            "Authorization": f"Bearer {CC_API_KEY}",
            "Content-Type": "application/json"
        }
        job_data = {
            "tasks": {
                "upload":  {"operation": "import/upload"},
                "convert": {
                    "operation": "convert",
                    "input": ["upload"],
                    "input_format": input_format,
                    "output_format": output_format
                },
                "export":  {"operation": "export/url", "input": ["convert"]}
            }
        }

        print("📦 Creando job en CloudConvert...")
        resp = requests.post("https://api.cloudconvert.com/v2/jobs", json=job_data, headers=cc_headers)
        if resp.status_code != 201:
            raise Exception(f"Error al crear job: {resp.text}")

        job = resp.json()
        job_id = job.get('data', {}).get('id')
        if not job_id:
            raise Exception(f"No se obtuvo job_id: {job}")
        print(f"✅ Job ID: {job_id}")

        # 3. Obtener URL de subida
        tasks_list  = job.get('data', {}).get('tasks', [])
        upload_task = next((t for t in tasks_list if t.get('operation') == 'import/upload'), None)
        if not upload_task:
            raise Exception("No se encontró tarea import/upload")

        result      = upload_task.get('result', {})
        form_data   = result.get('form', {})
        upload_url  = form_data.get('url')
        form_params = form_data.get('parameters', {})

        if not upload_url:
            raise Exception(f"No se encontró URL de subida. result={result}")

        # 4. Subir archivo a CloudConvert
        data_params = {}
        for k, v in form_params.items():
            data_params[k] = v.replace('${filename}', nombre_seguro) if k == 'key' else v

        with open(input_path, 'rb') as f:
            file_content = f.read()

        up_resp = requests.post(
            upload_url,
            data=data_params,
            files={'file': (nombre_seguro, file_content, mime_type)}
        )
        print(f"📥 Subida: {up_resp.status_code}")
        if up_resp.status_code not in [200, 201, 204]:
            raise Exception(f"Error al subir: {up_resp.status_code} - {up_resp.text[:200]}")
        print("✅ Archivo subido")

        # 5. Polling hasta que termine la conversión
        print("🔄 Convirtiendo...")
        poll_headers = {"Authorization": f"Bearer {CC_API_KEY}"}

        for i in range(90):
            time.sleep(2)
            status_resp = requests.get(
                f"https://api.cloudconvert.com/v2/jobs/{job_id}",
                headers=poll_headers
            )
            if not status_resp.ok:
                print(f"⚠️ Error consultando estado: {status_resp.status_code}")
                continue

            job_status = status_resp.json()
            current_tasks = job_status.get('data', {}).get('tasks', [])

            for task in current_tasks:
                if task.get('operation') == 'export/url' and task.get('status') == 'finished':
                    files_result = task.get('result', {}).get('files', [])
                    if files_result and 'url' in files_result[0]:
                        output_url  = files_result[0]['url']
                        out_filename = nombre_seguro.rsplit('.', 1)[0] + ext_salida
                        out_path     = os.path.join(UPLOAD_FOLDER, out_filename)

                        out_resp = requests.get(output_url)
                        if out_resp.status_code == 200:
                            with open(out_path, 'wb') as f:
                                f.write(out_resp.content)
                            print(f"✅ Conversión completada: {out_filename}")
                            link = f"{BASE_URL}/download/{out_filename}"
                            enviar_mensaje_texto(
                                telefono,
                                f"✅ *¡Listo!*\n📄 {out_filename}\n🔗 {link}\n⏰ El link expira en 10 minutos"
                            )
                            threading.Thread(target=programar_borrado, args=(input_path,)).start()
                            threading.Thread(target=programar_borrado, args=(out_path,)).start()
                            return

            if i % 10 == 0:
                print(f"⏳ Esperando... ({i * 2}s)")

        raise Exception("Tiempo de espera agotado")

    except Exception as e:
        print(f"❌ Error en conversión: {e}")
        import traceback; traceback.print_exc()
        enviar_mensaje_texto(telefono, f"❌ Error al convertir: {str(e)[:150]}")


# ─────────────────────────────────────────
# WEBHOOK
# ─────────────────────────────────────────

@app.route('/webhook', methods=['GET'])
def verificar_token():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Error de validación", 403


@app.route('/webhook', methods=['POST'])
def recibir_notificacion():
    data = request.get_json()
    print("=== 📩 Webhook ===")

    try:
        entry   = data['entry'][0]['changes'][0]['value']
        if 'messages' not in entry:
            return "OK", 200

        mensaje   = entry['messages'][0]
        remitente = mensaje['from']
        print(f"📱 De: {remitente}")

        # ── MENSAJE DE TEXTO ──────────────────────────────
        if 'text' in mensaje:
            cuerpo = mensaje['text']['body'].strip()
            print(f"💬 Texto: {cuerpo}")

            # ¿Tiene sesión activa esperando respuesta?
            if remitente in sesiones:
                sesion   = sesiones[remitente]
                opciones = sesion['opciones']

                if cuerpo.isdigit() and 1 <= int(cuerpo) <= len(opciones):
                    idx    = int(cuerpo) - 1
                    opcion = opciones[idx]
                    del sesiones[remitente]

                    enviar_mensaje_texto(
                        remitente,
                        f"⏳ Convirtiendo a *{opcion['label']}*... 🔄"
                    )
                    threading.Thread(
                        target=procesar_y_convertir,
                        args=(
                            sesion['file_url'],
                            sesion['filename'],
                            opcion['input_format'],
                            opcion['output_format'],
                            opcion['ext_salida'],
                            opcion['mime_type'],
                            remitente
                        )
                    ).start()
                else:
                    nums = ", ".join(str(i+1) for i in range(len(opciones)))
                    enviar_mensaje_texto(remitente, f"⚠️ Responde con un número válido: {nums}")
            else:
                # Sin sesión activa → bienvenida
                enviar_mensaje_texto(remitente, MENSAJE_BIENVENIDA)

        # ── DOCUMENTO ────────────────────────────────────
        elif 'document' in mensaje or 'image' in mensaje:
            es_imagen = 'image' in mensaje

            if es_imagen:
                media    = mensaje['image']
                filename = f"imagen_{media['id']}.jpg"
            else:
                media    = mensaje['document']
                filename = media.get('filename', 'archivo')

            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            print(f"📄 Archivo: {filename} (ext: {ext})")

            opciones_ext = CONVERSIONES.get(ext)

            if not opciones_ext:
                enviar_mensaje_texto(
                    remitente,
                    f"⚠️ El formato *.{ext}* no está soportado.\n\n"
                    "Acepto: *DOCX, XLSX, PDF, JPG, PNG*"
                )
                return "OK", 200

            # Obtener URL del archivo desde Meta
            file_data = requests.get(
                f"https://graph.facebook.com/v18.0/{media['id']}",
                headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}
            ).json()

            if 'url' not in file_data:
                print(f"❌ Meta no devolvió URL: {file_data}")
                enviar_mensaje_texto(remitente, "❌ No se pudo obtener el archivo. Intenta de nuevo.")
                return "OK", 200

            # Construir lista de opciones de conversión
            opciones = []
            for conv in opciones_ext:
                input_fmt, output_fmt, ext_sal, label, mime = conv
                opciones.append({
                    "input_format":  input_fmt,
                    "output_format": output_fmt,
                    "ext_salida":    ext_sal,
                    "label":         label,
                    "mime_type":     mime,
                })

            # Guardar sesión
            sesiones[remitente] = {
                "file_url": file_data['url'],
                "filename": filename,
                "opciones": opciones,
            }

            # Si solo hay una opción, preguntar igual (extensible a futuro)
            menu = "\n".join(f"  *{i+1}* → {op['label']}" for i, op in enumerate(opciones))
            enviar_mensaje_texto(
                remitente,
                f"📂 Archivo recibido: *{filename}*\n\n"
                f"¿A qué formato quieres convertirlo?\n\n{menu}\n\n"
                "Responde con el número de tu elección."
            )

    except Exception as e:
        print(f"❌ Error webhook: {e}")
        import traceback; traceback.print_exc()

    return "OK", 200


# ─────────────────────────────────────────
# DESCARGA
# ─────────────────────────────────────────

@app.route('/download/<filename>')
def descargar_archivo(filename):
    ruta = os.path.join(UPLOAD_FOLDER, filename)
    print(f"📂 Descarga: {filename} — existe: {os.path.exists(ruta)}")
    if not os.path.exists(ruta):
        return f"❌ Archivo no encontrado: {filename}", 404
    return send_from_directory(os.path.abspath(UPLOAD_FOLDER), filename, as_attachment=True)


@app.route('/')
def home():
    return "🤖 PDFMagic Bot funcionando. Webhook en /webhook"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)