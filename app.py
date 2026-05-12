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
MAX_WORDS = 200
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
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
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
        api_key = CC_API_KEY
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # 3. Crear el job con las tareas
        job_data = {
            "tasks": {
                "import-file": {
                    "operation": "import/upload"
                },
                "convert-file": {
                    "operation": "convert",
                    "input": ["import-file"],
                    "input_format": "docx",
                    "output_format": "pdf"
                },
                "export-file": {
                    "operation": "export/url",
                    "input": ["convert-file"]
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
            print(f"❌ Error al crear job: {response.text}")
            raise Exception(f"Error al crear job: {response.status_code}")
        
        job = response.json()
        
        # === EXTRACCIÓN ROBUSTA DEL JOB ID ===
        job_id = None
        
        # Intento 1: Directamente en 'data.id'
        if 'data' in job and isinstance(job['data'], dict) and 'id' in job['data']:
            job_id = job['data']['id']
            print("✅ Job ID extraído de 'data.id'")
        # Intento 2: Directamente en 'id'
        elif 'id' in job:
            job_id = job['id']
            print("✅ Job ID extraído de 'id'")
        # Intento 3: Buscar en cualquier tarea
        else:
            tasks = job.get('data', {}).get('tasks', job.get('tasks', []))
            if tasks and 'job_id' in tasks[0]:
                job_id = tasks[0]['job_id']
                print("✅ Job ID extraído de la primera tarea")
        
        print(f"📋 Job ID extraído: {job_id}")
        
        if not job_id:
            raise Exception(f"No se pudo extraer job_id. Respuesta completa: {job}")
        
        # === OBTENER URL DE SUBIDA ===
        upload_url = None
        tasks_list = job.get('data', {}).get('tasks', job.get('tasks', []))
        
        for task in tasks_list:
            if task.get('operation') == 'import/upload':
                result = task.get('result', {})
                upload_url = result.get('url') or result.get('form', {}).get('url')
                if upload_url:
                    print("✅ URL de subida encontrada")
                    break
        
        if not upload_url:
            raise Exception("No se pudo encontrar la URL de subida en la respuesta")
        
        # 4. Subir el archivo con PUT
        print("📤 Subiendo archivo...")
        with open(input_path, 'rb') as f:
            upload_response = requests.put(
                upload_url,
                data=f.read(),
                headers={"Content-Type": "application/octet-stream"}
            )
        
        if upload_response.status_code not in [200, 201, 204]:
            raise Exception(f"Error al subir archivo: {upload_response.status_code}")
        
        print("✅ Archivo subido exitosamente")
        
        # 5. Esperar a que termine la conversión
        print("🔄 Convirtiendo archivo... (esto puede tomar un minuto)")
        max_attempts = 60
        
        for i in range(max_attempts):
            time.sleep(2)
            
            # Consultar el estado del job
            status_response = requests.get(
                f"https://api.cloudconvert.com/v2/jobs/{job_id}",
                headers=headers
            )
            
            if not status_response.ok:
                print(f"⚠️ Error al consultar estado: {status_response.status_code}")
                continue
            
            job_status = status_response.json()
            tasks_list = job_status.get('data', {}).get('tasks', job_status.get('tasks', []))
            
            for task in tasks_list:
                if task.get('operation') == 'export/url' and task.get('status') == 'finished':
                    files = task.get('result', {}).get('files', [])
                    if files and 'url' in files[0]:
                        pdf_url = files[0]['url']
                        
                        pdf_filename = nombre_original.rsplit('.', 1)[0] + ".pdf"
                        pdf_path = os.path.join(UPLOAD_FOLDER, pdf_filename)
                        
                        pdf_response = requests.get(pdf_url)
                        if pdf_response.status_code == 200:
                            with open(pdf_path, 'wb') as f:
                                f.write(pdf_response.content)
                            
                            print(f"✅ Conversión completada: {pdf_filename}")
                            
                            link = f"{BASE_URL}/download/{pdf_filename}"
                            enviar_mensaje_texto(telefono, f"✅ ¡Conversión lista!\n📄 {pdf_filename}\n🔗 {link}\n⏰ El link expirará en 5 minutos")
                            
                            threading.Thread(target=programar_borrado, args=(input_path,)).start()
                            threading.Thread(target=programar_borrado, args=(pdf_path,)).start()
                            return
            
            if i % 10 == 0:
                print(f"⏳ Esperando conversión... ({i*2} segundos)")
        
        raise Exception("Tiempo de espera agotado para la conversión")

    except Exception as e:
        print(f"❌ Error en conversión: {e}")
        import traceback
        traceback.print_exc()
        enviar_mensaje_texto(telefono, f"❌ Error: {str(e)[:150]}")

@app.route('/webhook', methods=['GET'])
def verificar_token():
    """Verificación del webhook por Meta"""
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Error de validación", 403

@app.route('/webhook', methods=['POST'])
def recibir_notificacion():
    """Recibe notificaciones de WhatsApp"""
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
                enviar_mensaje_texto(remitente, "🤖 *¡Hola! Soy tu bot conversor PDFMagic*\n\n📄 Envíame cualquier archivo WORD (.docx) y lo convertiré automáticamente a PDF.\n\n⚡ Sin registros, sin clics, sin complicaciones.")
                print("✅ Respuesta enviada")

            elif 'document' in mensaje:
                doc = mensaje['document']
                filename = doc.get('filename', 'documento.docx')
                print(f"📄 Documento: {filename}")
                
                # Obtener la URL del archivo desde Meta
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
                    print(f"❌ Error: No se encontró URL en {file_data}")
                    enviar_mensaje_texto(remitente, "❌ No se pudo obtener el archivo. Intenta de nuevo.")
            else:
                print("📊 Otro tipo de mensaje (ignorado)")
        
        elif 'statuses' in entry:
            print("📊 Actualización de estado (ignorado)")
        
        else:
            print("📊 Otro tipo de evento (ignorado)")
            
    except KeyError as e:
        print(f"❌ Error de clave: {e}")
    except Exception as e:
        print(f"❌ Error general: {e}")
        import traceback
        traceback.print_exc()
    
    return "OK", 200

@app.route('/download/<filename>')
def descargar_archivo(filename):
    """Descarga archivos convertidos"""
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/')
def home():
    """Página de inicio para verificar que el bot está vivo"""
    return "🤖 Bot de WhatsApp funcionando. Webhook en /webhook"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)