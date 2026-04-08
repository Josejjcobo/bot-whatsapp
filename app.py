import os
import time
import threading
import requests
import cloudconvert
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

# --- CONFIGURACIÓN DE VARIABLES ---
ACCESS_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
CC_API_KEY = os.environ.get("CLOUD_CONVERT_API_KEY")

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
    requests.post(url, headers=headers, json=payload)

def procesar_y_convertir(file_url, nombre_original, telefono):
    """Descarga de Meta, convierte en CloudConvert y programa limpieza"""
    try:
        # 1. Descargar el archivo desde los servidores de Meta
        r = requests.get(file_url, headers={"Authorization": f"Bearer {ACCESS_TOKEN}"})
        input_path = os.path.join(UPLOAD_FOLDER, nombre_original)
        with open(input_path, 'wb') as f:
            f.write(r.content)
        
        # 2. Convertir usando CloudConvert (DOCX a PDF)
        api = cloudconvert.Api(api_key=CC_API_KEY)
        process = api.convert({
            "inputformat": "docx",
            "outputformat": "pdf",
            "input": "upload",
            "file": open(input_path, 'rb')
        })
        
        pdf_filename = nombre_original.rsplit('.', 1)[0] + ".pdf"
        pdf_path = os.path.join(UPLOAD_FOLDER, pdf_filename)
        process.wait()
        process.download(pdf_path)
        
        # 3. Generar link de descarga (Render)
        link = f"https://{request.host}/download/{pdf_filename}"
        enviar_mensaje_texto(telefono, f"✅ ¡Conversión lista!\nDescarga aquí: {link}\n(El link expirará en 5 minutos)")

        # 4. Lanzar hilos de borrado
        threading.Thread(target=programar_borrado, args=(input_path,)).start()
        threading.Thread(target=programar_borrado, args=(pdf_path,)).start()

    except Exception as e:
        enviar_mensaje_texto(telefono, f"❌ Error en el proceso: {str(e)}")

@app.route('/webhook', methods=['GET'])
def verificar_token():
    # Validación obligatoria para configurar el Webhook en Meta
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Error de validación", 403

@app.route('/webhook', methods=['POST'])
def recibir_notificacion():
    data = request.get_json()
    try:
        entry = data['entry'][0]['changes'][0]['value']
        if 'messages' in entry:
            mensaje = entry['messages'][0]
            remitente = mensaje['from']

            # VALIDACIÓN: LÍMITE DE PALABRAS
            if 'text' in mensaje:
                cuerpo = mensaje['text']['body']
                if len(cuerpo.split()) > MAX_WORDS:
                    enviar_mensaje_texto(remitente, f"⚠️ El mensaje es muy largo. Máximo {MAX_WORDS} palabras.")
                else:
                    enviar_mensaje_texto(remitente, "¡Hola! Soy tu bot conversor. Envíame un archivo .docx para empezar.")

            # VALIDACIÓN: LÍMITE DE PESO Y TIPO DE ARCHIVO
            elif 'document' in mensaje:
                doc = mensaje['document']
                if doc['file_size'] > MAX_FILE_SIZE:
                    enviar_mensaje_texto(remitente, "❌ El archivo pesa más de 10 MB.")
                else:
                    # Obtener la URL de descarga del archivo desde la API de Meta
                    file_data = requests.get(f"https://graph.facebook.com/v18.0/{doc['id']}", 
                                           headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}).json()
                    
                    enviar_mensaje_texto(remitente, "⏳ Recibido. Estoy convirtiendo tu archivo...")
                    
                    threading.Thread(target=procesar_y_convertir, 
                                   args=(file_data['url'], doc['filename'], remitente)).start()

    except:
        pass
    return "OK", 200

@app.route('/download/<filename>')
def descargar_archivo(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
