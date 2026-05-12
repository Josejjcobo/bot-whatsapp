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
        
        # Extraer job ID
        job_id = job.get('data', {}).get('id')
        if not job_id:
            raise Exception(f"No se pudo extraer job_id. Respuesta: {job}")
        
        print(f"✅ Job ID: {job_id}")
        
        # Obtener URL de subida y parámetros del formulario
        upload_data = None
        tasks_list = job.get('data', {}).get('tasks', [])
        
        for task in tasks_list:
            if task.get('operation') == 'import/upload':
                upload_data = task.get('result', {})
                if upload_data:
                    print("✅ Datos de subida obtenidos")
                    break
        
        if not upload_data:
            raise Exception("No se pudo obtener la información de subida")
        
        # La URL puede estar en 'url' o en 'form.url'
        upload_url = upload_data.get('url') or upload_data.get('form', {}).get('url')
        if not upload_url:
            raise Exception("No se pudo encontrar la URL de subida")
        
        print(f"📤 Subiendo archivo a CloudConvert...")
        
        # Construir el multipart/form-data correctamente
        # Los parámetros del formulario están en upload_data.get('form', {})
        form_params = upload_data.get('form', {})
        
        # Crear el archivo para subir
        files = {
            'file': (nombre_original, open(input_path, 'rb'), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        }
        
        # Agregar todos los parámetros del formulario
        data_params = {}
        for key, value in form_params.items():
            data_params[key] = value
        
        # Subir con POST usando multipart/form-data
        upload_response = requests.post(
            upload_url,
            data=data_params,
            files=files
        )
        
        print(f"📥 Respuesta subida: {upload_response.status_code}")
        
        if upload_response.status_code not in [200, 201, 204]:
            raise Exception(f"Error al subir archivo: {upload_response.status_code} - {upload_response.text[:200]}")
        
        print("✅ Archivo subido exitosamente")
        
        # 4. Esperar a que termine la conversión
        print("🔄 Convirtiendo archivo... (esto puede tomar un minuto)")
        max_attempts = 90
        
        for i in range(max_attempts):
            time.sleep(2)
            
            status_response = requests.get(
                f"https://api.cloudconvert.com/v2/jobs/{job_id}",
                headers=headers
            )
            
            if not status_response.ok:
                print(f"⚠️ Error al consultar estado: {status_response.status_code}")
                continue
            
            job_status = status_response.json()
            tasks_list = job_status.get('data', {}).get('tasks', [])
            
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
                enviar_mensaje_texto(remitente, "🤖 *¡Hola! Soy tu bot conversor PDFMagic*\n\n📄 Envíame cualquier archivo WORD (.docx) y lo convertiré automáticamente a PDF.\n\n⚡ Sin registros, sin clics, sin complicaciones.")

            elif 'document' in mensaje:
                doc = mensaje['document']
                filename = doc.get('filename', 'documento.docx')
                print(f"📄 Documento: {filename}")
                
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
                    enviar_mensaje_texto(remitente, "❌ No se pudo obtener el archivo. Intenta de nuevo.")
        
    except Exception as e:
        print(f"❌ Error: {e}")
    
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