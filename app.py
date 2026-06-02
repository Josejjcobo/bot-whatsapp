import os
import time
import threading
import requests
from flask import Flask, request, send_from_directory

app = Flask(__name__)

# --- CONFIGURACIÓN DE VARIABLES ---
ACCESS_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
CC_API_KEY = os.environ.get("CLOUD_CONVERT_API_KEY")
BASE_URL = os.environ.get("BASE_URL", "https://bot-whatsapp-zcek.onrender.com")

# --- LÍMITES Y RUTAS ---
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
UPLOAD_FOLDER = '/tmp/archivos_bot'

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


def programar_borrado(ruta):
    """Espera 5 minutos y elimina el archivo del servidor"""
    time.sleep(300)
    if os.path.exists(ruta):
        os.remove(ruta)
        print(f"🧹 Limpieza automática: {ruta} eliminado.")


def enviar_mensaje_texto(receptor, texto):
    """Envía una respuesta rápida de texto vía API de Meta"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": receptor,
        "type": "text",
        "text": {"body": texto}
    }
    try:
        response = requests.post(url, headers=headers, json=payload)
        print(f"📤 Mensaje enviado a {receptor}: {response.status_code}")
        if response.status_code != 200:
            print(f"Error respuesta: {response.text}")
    except Exception as e:
        print(f"❌ Error al enviar mensaje: {e}")


def procesar_y_convertir(file_url, nombre_original, telefono):
    """Descarga de Meta, convierte en CloudConvert y programa limpieza"""
    try:
        # 1. Descargar el archivo desde los servidores de Meta
        r = requests.get(file_url, headers={"Authorization": f"Bearer {ACCESS_TOKEN}"})
        input_path = os.path.join(UPLOAD_FOLDER, nombre_original)
        with open(input_path, 'wb') as f:
            f.write(r.content)
        print(f"📥 Archivo descargado: {nombre_original}")

        # 2. Configurar headers para CloudConvert
        headers = {
            "Authorization": f"Bearer {CC_API_KEY}",
            "Content-Type": "application/json"
        }

        # 3. Crear el job con las tareas
        job_data = {
            "tasks": {
                "upload": {
                    "operation": "import/upload"
                },
                "convert": {
                    "operation": "convert",
                    "input": ["upload"],
                    "input_format": "docx",
                    "output_format": "pdf"
                },
                "export": {
                    "operation": "export/url",
                    "input": ["convert"]
                }
            }
        }

        print("📦 Creando job en CloudConvert...")
        response = requests.post(
            "https://api.cloudconvert.com/v2/jobs",
            json=job_data,
            headers=headers
        )
        print(f"📥 Respuesta CloudConvert status: {response.status_code}")

        if response.status_code != 201:
            raise Exception(f"Error al crear job: {response.text}")

        job = response.json()
        print(f"📋 Job response: {job}")

        # Extraer job ID
        job_id = job.get('data', {}).get('id')
        if not job_id:
            raise Exception(f"No se pudo extraer job_id. Respuesta: {job}")
        print(f"✅ Job ID: {job_id}")

        # Obtener la tarea de import/upload
        tasks_list = job.get('data', {}).get('tasks', [])
        upload_task = next(
            (t for t in tasks_list if t.get('operation') == 'import/upload'),
            None
        )

        if not upload_task:
            raise Exception("No se encontró la tarea de import/upload")

        # ✅ CORRECCIÓN PRINCIPAL:
        # La URL está en result.form.url (NO en result.url)
        # Los parámetros están en result.form.parameters (NO en result.form)
        result = upload_task.get('result', {})
        form_data = result.get('form', {})
        upload_url = form_data.get('url')
        form_params = form_data.get('parameters', {})

        if not upload_url:
            raise Exception(f"No se pudo encontrar la URL de subida. result={result}")

        print(f"📤 URL de subida: {upload_url}")
        print(f"📋 Parámetros del formulario: {form_params}")

        # 4. Construir parámetros y reemplazar ${filename} en el campo key
        data_params = {}
        for key, value in form_params.items():
            if key == 'key':
                value = value.replace('${filename}', nombre_original)
            data_params[key] = value

        # ✅ Leer el archivo primero para evitar que se cierre antes del POST
        with open(input_path, 'rb') as f:
            file_content = f.read()

        upload_response = requests.post(
            upload_url,
            data=data_params,
            files={
                'file': (
                    nombre_original,
                    file_content,
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                )
            }
        )

        print(f"📥 Respuesta subida: {upload_response.status_code}")
        print(f"📥 Respuesta texto: {upload_response.text[:500]}")

        if upload_response.status_code not in [200, 201, 204]:
            raise Exception(f"Error al subir archivo: {upload_response.status_code} - {upload_response.text[:200]}")

        print("✅ Archivo subido exitosamente")

        # 5. Esperar a que termine la conversión (polling)
        print("🔄 Convirtiendo archivo... (esto puede tomar un minuto)")
        poll_headers = {"Authorization": f"Bearer {CC_API_KEY}"}

        for i in range(90):
            time.sleep(2)

            status_response = requests.get(
                f"https://api.cloudconvert.com/v2/jobs/{job_id}",
                headers=poll_headers
            )

            if not status_response.ok:
                print(f"⚠️ Error al consultar estado: {status_response.status_code}")
                continue

            job_status = status_response.json()
            tasks_list = job_status.get('data', {}).get('tasks', [])

            for task in tasks_list:
                if task.get('operation') == 'export/url' and task.get('status') == 'finished':
                    files_result = task.get('result', {}).get('files', [])
                    if files_result and 'url' in files_result[0]:
                        pdf_url = files_result[0]['url']
                        pdf_filename = nombre_original.rsplit('.', 1)[0] + ".pdf"
                        pdf_path = os.path.join(UPLOAD_FOLDER, pdf_filename)

                        pdf_response = requests.get(pdf_url)
                        if pdf_response.status_code == 200:
                            with open(pdf_path, 'wb') as f:
                                f.write(pdf_response.content)

                            print(f"✅ Conversión completada: {pdf_filename}")
                            link = f"{BASE_URL}/download/{pdf_filename}"
                            enviar_mensaje_texto(
                                telefono,
                                f"✅ ¡Conversión lista!\n📄 {pdf_filename}\n🔗 {link}\n⏰ El link expirará en 5 minutos"
                            )
                            threading.Thread(target=programar_borrado, args=(input_path,)).start()
                            threading.Thread(target=programar_borrado, args=(pdf_path,)).start()
                            return

            # Verificar si el job falló
            job_overall_status = job_status.get('data', {}).get('status')
            if job_overall_status == 'error':
                error_task = next(
                    (t for t in tasks_list if t.get('status') == 'error'),
                    None
                )
                error_msg = error_task.get('message', 'Error desconocido') if error_task else 'Error desconocido'
                raise Exception(f"CloudConvert reportó error: {error_msg}")

            if i % 10 == 0:
                print(f"⏳ Esperando conversión... ({i * 2} segundos)")

        raise Exception("Tiempo de espera agotado para la conversión")

    except Exception as e:
        print(f"❌ Error en conversión: {e}")
        import traceback
        traceback.print_exc()
        enviar_mensaje_texto(telefono, f"❌ Error al convertir: {str(e)[:150]}")


@app.route('/webhook', methods=['GET'])
def verificar_token():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Error de validación", 403


@app.route('/webhook', methods=['POST'])
def recibir_notificacion():
    data = request.get_json()
    print("=== 📩 Webhook recibido ===")

    try:
        entry = data['entry'][0]['changes'][0]['value']

        if 'messages' in entry:
            mensaje = entry['messages'][0]
            remitente = mensaje['from']
            print(f"📱 Mensaje de: {remitente}")

            if 'text' in mensaje:
                cuerpo = mensaje['text']['body']
                print(f"💬 Texto: {cuerpo}")
                enviar_mensaje_texto(
                    remitente,
                    "🤖 *¡Hola! Soy tu bot conversor PDFMagic*\n\n"
                    "📄 Envíame cualquier archivo WORD (.docx) y lo convertiré automáticamente a PDF.\n\n"
                    "⚡ Sin registros, sin clics, sin complicaciones."
                )

            elif 'document' in mensaje:
                doc = mensaje['document']
                filename = doc.get('filename', 'documento.docx')
                print(f"📄 Documento recibido: {filename}")

                # Verificar que sea un .docx
                if not filename.lower().endswith('.docx'):
                    enviar_mensaje_texto(
                        remitente,
                        "⚠️ Solo acepto archivos WORD (.docx). Por favor envía un archivo con esa extensión."
                    )
                    return "OK", 200

                # Obtener URL del archivo desde la API de Meta
                file_data = requests.get(
                    f"https://graph.facebook.com/v18.0/{doc['id']}",
                    headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}
                ).json()

                if 'url' in file_data:
                    enviar_mensaje_texto(remitente, "⏳ ¡Recibido! Estoy convirtiendo tu archivo a PDF... 🔄")
                    threading.Thread(
                        target=procesar_y_convertir,
                        args=(file_data['url'], filename, remitente)
                    ).start()
                else:
                    print(f"❌ Respuesta de Meta sin URL: {file_data}")
                    enviar_mensaje_texto(remitente, "❌ No se pudo obtener el archivo. Intenta de nuevo.")

    except Exception as e:
        print(f"❌ Error en webhook: {e}")
        import traceback
        traceback.print_exc()

    return "OK", 200


@app.route('/download/<filename>')
def descargar_archivo(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route('/')
def home():
    return "🤖 Bot de WhatsApp funcionando. Webhook en /webhook"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
