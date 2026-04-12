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
MAX_FILE_SIZE = 10 * 1024 * 1024
UPLOAD_FOLDER = '/tmp/archivos_bot'

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def programar_borrado(ruta):
    time.sleep(300)
    if os.path.exists(ruta):
        os.remove(ruta)
        print(f"🧹 Limpieza: {ruta} eliminado.")

def enviar_mensaje_texto(receptor, texto):
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
        print(f"📤 Mensaje a {receptor}: {response.status_code}")
    except Exception as e:
        print(f"❌ Error: {e}")

def procesar_y_convertir(file_url, nombre_original, telefono):
    try:
        # 1. Descargar archivo
        r = requests.get(file_url, headers={"Authorization": f"Bearer {ACCESS_TOKEN}"})
        input_path = os.path.join(UPLOAD_FOLDER, nombre_original)
        with open(input_path, 'wb') as f:
            f.write(r.content)
        print(f"📥 Descargado: {nombre_original}")
        
        # 2. Configurar CloudConvert
        api_key = CC_API_KEY
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # 3. Crear job de conversión (estructura más simple)
        job_data = {
            "tasks": {
                "upload": {"operation": "import/upload"},
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
        
        print("📦 Creando job...")
        response = requests.post("https://api.cloudconvert.com/v2/jobs", json=job_data, headers=headers)
        
        print(f"Status: {response.status_code}")
        print(f"Respuesta: {response.text}")
        
        if response.status_code != 201:
            raise Exception(f"Error: {response.text}")
        
        job = response.json()
        job_id = job.get('id')
        
        if not job_id:
            raise Exception(f"No job ID. Respuesta: {job}")
        
        print(f"✅ Job ID: {job_id}")
        
        # 4. Obtener URL de subida
        upload_url = None
        for task in job.get('tasks', []):
            if task.get('name') == 'upload':
                result = task.get('result', {})
                if 'url' in result:
                    upload_url = result['url']
                elif 'form' in result and 'url' in result['form']:
                    upload_url = result['form']['url']
                break
        
        if not upload_url:
            raise Exception("No se encontró URL de subida")
        
        print("📤 Subiendo archivo...")
        with open(input_path, 'rb') as f:
            put_response = requests.put(upload_url, data=f.read())
        
        if put_response.status_code not in [200, 201, 204]:
            raise Exception(f"Error subida: {put_response.status_code}")
        
        print("✅ Archivo subido")
        
        # 5. Esperar conversión
        print("🔄 Esperando conversión...")
        for i in range(60):
            time.sleep(2)
            resp = requests.get(f"https://api.cloudconvert.com/v2/jobs/{job_id}", headers=headers)
            status = resp.json()
            
            for task in status.get('tasks', []):
                if task.get('name') == 'export' and task.get('status') == 'finished':
                    pdf_url = task['result']['files'][0]['url']
                    pdf_filename = nombre_original.replace('.docx', '.pdf')
                    pdf_path = os.path.join(UPLOAD_FOLDER, pdf_filename)
                    
                    pdf_resp = requests.get(pdf_url)
                    with open(pdf_path, 'wb') as f:
                        f.write(pdf_resp.content)
                    
                    link = f"{BASE_URL}/download/{pdf_filename}"
                    enviar_mensaje_texto(telefono, f"✅ ¡Conversión lista!\n📄 {pdf_filename}\n🔗 {link}")
                    
                    threading.Thread(target=programar_borrado, args=(input_path,)).start()
                    threading.Thread(target=programar_borrado, args=(pdf_path,)).start()
                    return
        
        raise Exception("Tiempo agotado")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        enviar_mensaje_texto(telefono, f"❌ Error: {str(e)[:100]}")

@app.route('/webhook', methods=['GET'])
def verificar_token():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Error de validación", 403

@app.route('/webhook', methods=['POST'])
def recibir_notificacion():
    data = request.get_json()
    print("=== Webhook recibido ===")
    
    try:
        entry = data['entry'][0]['changes'][0]['value']
        
        if 'messages' in entry:
            mensaje = entry['messages'][0]
            remitente = mensaje['from']
            
            if 'text' in mensaje:
                cuerpo = mensaje['text']['body']
                enviar_mensaje_texto(remitente, "🤖 ¡Hola! Envíame un archivo .docx y lo convierto a PDF.")
            
            elif 'document' in mensaje:
                doc = mensaje['document']
                filename = doc.get('filename', 'documento.docx')
                
                file_data = requests.get(
                    f"https://graph.facebook.com/v18.0/{doc['id']}",
                    headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}
                ).json()
                
                if 'url' in file_data:
                    enviar_mensaje_texto(remitente, "⏳ Recibido. Convirtiendo...")
                    threading.Thread(target=procesar_y_convertir, args=(file_data['url'], filename, remitente)).start()
                else:
                    enviar_mensaje_texto(remitente, "❌ Error al obtener el archivo")
    
    except Exception as e:
        print(f"Error: {e}")
    
    return "OK", 200

@app.route('/download/<filename>')
def descargar_archivo(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/')
def home():
    return "🤖 Bot funcionando"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)