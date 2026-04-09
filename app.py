import os
import time
import threading
import requests
import cloudconvert
from flask import Flask, request, send_from_directory

app = Flask(__name__)

# --- CONFIGURACIÓN DE VARIABLES ---
ACCESS_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
CC_API_KEY = os.environ.get("CLOUD_CONVERT_API_KEY")
BASE_URL = os.environ.get("BASE_URL", "https://bot-whatsapp-mljx.onrender.com")

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
        
        print(f"✅ Conversión completada: {pdf_filename}")
        
        # 3. Generar link de descarga
        link = f"{BASE_URL}/download/{pdf_filename}"
        enviar_mensaje_texto(telefono, f"✅ ¡Conversión lista!\n📄 {pdf_filename}\n🔗 {link}\n⏰ El link expirará en 5 minutos")

        # 4. Lanzar hilos de borrado
        threading.Thread(target=programar_borrado, args=(input_path,)).start()
        threading.Thread(target=programar_borrado, args=(pdf_path,)).start()

    except Exception as e:
        print(f"❌ Error en conversión: {e}")
        enviar_mensaje_texto(telefono, f"❌ Error: {str(e)}")

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
    print(data)
    
    try:
        # Extraer el mensaje
        entry = data['entry'][0]['changes'][0]['value']
        
        if 'messages' in entry:
            mensaje = entry['messages'][0]
            remitente = mensaje['from']
            print(f"📱 Mensaje de: {remitente}")

            # Mensaje de texto
            if 'text' in mensaje:
                cuerpo = mensaje['text']['body']
                print(f"💬 Texto: {cuerpo}")
                
                if len(cuerpo.split()) > MAX_WORDS:
                    enviar_mensaje_texto(remitente, f"⚠️ El mensaje es muy largo. Máximo {MAX_WORDS} palabras.")
                else:
                    # NUEVO MENSAJE DE BIENVENIDA
                    enviar_mensaje_texto(remitente, "🤖 *¡Hola! Soy tu bot conversor PDFMagic*\n\n📄 Envíame cualquier archivo WORD (.docx) y lo convertiré automáticamente a PDF.\n\n⚡ Sin registros, sin clics, sin complicaciones.\n\n🔒 Tus archivos se eliminan después de 5 minutos.\n\n✨ ¡Solo envía tu documento y yo hago el resto!")
                print("✅ Respuesta enviada")

            # Mensaje con documento
            elif 'document' in mensaje:
                doc = mensaje['document']
                
                # Mostrar todo el documento para depuración
                print(f"📄 Documento recibido: {doc}")
                
                # Obtener el tamaño de forma segura (puede venir en diferentes campos)
                file_size = 0
                if 'file_size' in doc:
                    file_size = doc['file_size']
                elif 'size' in doc:
                    file_size = doc['size']
                
                # Obtener el nombre del archivo
                filename = doc.get('filename', 'documento.docx')
                
                print(f"📄 Archivo: {filename}, Tamaño: {file_size} bytes")
                
                # Verificar límite de tamaño (si tenemos el tamaño)
                if file_size > 0 and file_size > MAX_FILE_SIZE:
                    enviar_mensaje_texto(remitente, f"❌ El archivo pesa más de 10 MB. Pesa {file_size // (1024*1024)} MB")
                else:
                    try:
                        # Obtener la URL de descarga del archivo desde la API de Meta
                        file_data = requests.get(
                            f"https://graph.facebook.com/v18.0/{doc['id']}", 
                            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}
                        ).json()
                        
                        print(f"📥 Datos del archivo: {file_data}")
                        
                        if 'url' in file_data:
                            enviar_mensaje_texto(remitente, "⏳ ¡Recibido! Estoy convirtiendo tu archivo a PDF...\n\n🔄 Procesando...")
                            threading.Thread(
                                target=procesar_y_convertir, 
                                args=(file_data['url'], filename, remitente)
                            ).start()
                        else:
                            print(f"❌ Error: No se encontró URL en la respuesta: {file_data}")
                            enviar_mensaje_texto(remitente, "❌ No se pudo obtener el archivo. Intenta de nuevo.")
                    except Exception as e:
                        print(f"❌ Error al obtener el archivo: {e}")
                        enviar_mensaje_texto(remitente, f"❌ Error al procesar el archivo: {str(e)}")
            else:
                print("📊 Otro tipo de mensaje (ignorado)")
        
        elif 'statuses' in entry:
            # Actualizaciones de estado de mensajes (entregado, leído, etc.)
            print("📊 Actualización de estado (ignorado)")
        
        else:
            print("📊 Otro tipo de evento (ignorado)")
            
    except KeyError as e:
        print(f"❌ Error de clave: {e}. Revisa la estructura del JSON")
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