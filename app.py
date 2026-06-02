import os
import time
import threading
import unicodedata
import requests
import google.generativeai as genai
import PyPDF2
from flask import Flask, request, send_from_directory

app = Flask(__name__)

# --- CONFIGURACIÓN DE VARIABLES ---
ACCESS_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_ID     = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
CC_API_KEY   = os.environ.get("CLOUD_CONVERT_API_KEY")
BASE_URL     = os.environ.get("BASE_URL", "https://bot-whatsapp-zcek.onrender.com")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- CONFIGURAR GEMINI IA ---
gemini_model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash')
        print("🤖 Gemini IA configurada correctamente")
    except Exception as e:
        print(f"⚠️ Error configurando Gemini: {e}")
else:
    print("⚠️ GEMINI_API_KEY no configurada")

# --- RUTAS ---
UPLOAD_FOLDER = '/tmp/archivos_bot'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- ESTADO POR USUARIO ---
sesiones = {}

# --- CONVERSIONES SOPORTADAS ---
CONVERSIONES = {
    "docx": [("docx", "pdf", ".pdf", "PDF", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")],
    "xlsx": [("xlsx", "pdf", ".pdf", "PDF", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
    "pdf":  [("pdf", "docx", ".docx", "Word (DOCX)", "application/pdf")],
    "jpg":  [("jpg", "pdf", ".pdf", "PDF", "image/jpeg")],
    "jpeg": [("jpeg", "pdf", ".pdf", "PDF", "image/jpeg")],
    "png":  [("png", "pdf", ".pdf", "PDF", "image/png")],
}

MENSAJE_BIENVENIDA = (
    "🤖 *¡Hola! Soy PDFMagic Bot*\n\n"
    "Puedo hacer lo siguiente:\n\n"
    "📄 *DOCX* → PDF\n"
    "📊 *XLSX* → PDF\n"
    "🖼️ *JPG/PNG* → PDF\n"
    "📑 *PDF* → Word (DOCX)\n"
    "🤖 *PDF* → Resumir con IA\n"
    "📝 *PDF* → Extraer texto\n\n"
    "Envía un archivo y te ayudo."
)

# ─────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────

def sanitizar_nombre(nombre):
    n = unicodedata.normalize('NFKD', nombre)
    n = n.encode('ascii', 'ignore').decode('ascii')
    return n.replace(' ', '_')

def programar_borrado(ruta):
    time.sleep(600)
    if os.path.exists(ruta):
        os.remove(ruta)
        print(f"🧹 Limpieza: {ruta} eliminado.")

def enviar_mensaje_texto(receptor, texto):
    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": receptor, "type": "text", "text": {"body": texto}}
    try:
        r = requests.post(url, headers=headers, json=payload)
        print(f"📤 Mensaje a {receptor}: {r.status_code}")
    except Exception as e:
        print(f"❌ Error: {e}")

def extraer_texto_pdf(pdf_path):
    """Extrae texto de un PDF"""
    texto = ""
    try:
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    texto += page_text + "\n"
        return texto if texto.strip() else None
    except Exception as e:
        print(f"❌ Error extrayendo texto: {e}")
        return None

def resumir_con_gemini(texto):
    """Resume texto usando Gemini"""
    if not gemini_model:
        return "❌ IA no disponible"
    
    if len(texto) > 8000:
        texto = texto[:8000]
    
    prompt = f"Resume el siguiente texto de forma clara y concisa:\n\n{texto}\n\nResumen:"
    try:
        respuesta = gemini_model.generate_content(prompt)
        return respuesta.text
    except Exception as e:
        return f"❌ Error: {str(e)[:100]}"

# ─────────────────────────────────────────
# CONVERSIÓN CLOUDCONVERT
# ─────────────────────────────────────────

def procesar_y_convertir(file_url, nombre_original, input_format, output_format, ext_salida, mime_type, telefono):
    """Convierte archivo con CloudConvert"""
    try:
        nombre_seguro = sanitizar_nombre(nombre_original)
        r = requests.get(file_url, headers={"Authorization": f"Bearer {ACCESS_TOKEN}"})
        input_path = os.path.join(UPLOAD_FOLDER, nombre_seguro)
        with open(input_path, 'wb') as f:
            f.write(r.content)
        print(f"📥 Descargado: {nombre_seguro}")

        cc_headers = {"Authorization": f"Bearer {CC_API_KEY}", "Content-Type": "application/json"}
        job_data = {
            "tasks": {
                "upload": {"operation": "import/upload"},
                "convert": {"operation": "convert", "input": ["upload"], "input_format": input_format, "output_format": output_format},
                "export": {"operation": "export/url", "input": ["convert"]}
            }
        }

        resp = requests.post("https://api.cloudconvert.com/v2/jobs", json=job_data, headers=cc_headers)
        if resp.status_code != 201:
            raise Exception(f"Error crear job: {resp.text}")

        job = resp.json()
        job_id = job.get('data', {}).get('id')
        if not job_id:
            raise Exception("No job_id")

        tasks = job.get('data', {}).get('tasks', [])
        upload_task = next((t for t in tasks if t.get('operation') == 'import/upload'), None)
        if not upload_task:
            raise Exception("No upload task")

        form_data = upload_task.get('result', {}).get('form', {})
        upload_url = form_data.get('url')
        form_params = form_data.get('parameters', {})

        data_params = {k: v.replace('${filename}', nombre_seguro) if k == 'key' else v for k, v in form_params.items()}

        with open(input_path, 'rb') as f:
            up_resp = requests.post(upload_url, data=data_params, files={'file': (nombre_seguro, f.read(), mime_type)})

        if up_resp.status_code not in [200, 201, 204]:
            raise Exception(f"Error subida: {up_resp.status_code}")

        for i in range(90):
            time.sleep(2)
            status_resp = requests.get(f"https://api.cloudconvert.com/v2/jobs/{job_id}", headers={"Authorization": f"Bearer {CC_API_KEY}"})
            if not status_resp.ok:
                continue
            job_status = status_resp.json()
            for task in job_status.get('data', {}).get('tasks', []):
                if task.get('operation') == 'export/url' and task.get('status') == 'finished':
                    files = task.get('result', {}).get('files', [])
                    if files and 'url' in files[0]:
                        out_url = files[0]['url']
                        out_filename = nombre_seguro.rsplit('.', 1)[0] + ext_salida
                        out_path = os.path.join(UPLOAD_FOLDER, out_filename)
                        out_resp = requests.get(out_url)
                        if out_resp.status_code == 200:
                            with open(out_path, 'wb') as f:
                                f.write(out_resp.content)
                            link = f"{BASE_URL}/download/{out_filename}"
                            enviar_mensaje_texto(telefono, f"✅ ¡Conversión lista!\n📄 {out_filename}\n🔗 {link}")
                            threading.Thread(target=programar_borrado, args=(input_path,)).start()
                            threading.Thread(target=programar_borrado, args=(out_path,)).start()
                            return
        raise Exception("Tiempo agotado")
    except Exception as e:
        print(f"❌ Error: {e}")
        enviar_mensaje_texto(telefono, f"❌ Error: {str(e)[:150]}")

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
        entry = data['entry'][0]['changes'][0]['value']
        if 'messages' not in entry:
            return "OK", 200

        mensaje = entry['messages'][0]
        remitente = mensaje['from']
        print(f"📱 De: {remitente}")

        # --- SI ES TEXTO ---
        if 'text' in mensaje:
            cuerpo = mensaje['text']['body'].strip()
            print(f"💬 Texto: {cuerpo}")

            # Si está esperando respuesta de IA
            if remitente in sesiones and sesiones[remitente].get('esperando') == 'opcion_ia':
                if cuerpo in ['1', '2', '3']:
                    sesion = sesiones[remitente]
                    enviar_mensaje_texto(remitente, "⏳ Procesando...")
                    
                    if cuerpo == '1':
                        # Convertir a Word
                        threading.Thread(target=procesar_y_convertir, args=(
                            sesion['file_url'], sesion['filename'], 'pdf', 'docx', '.docx', 'application/pdf', remitente
                        )).start()
                    elif cuerpo == '2':
                        # Resumir con IA
                        r = requests.get(sesion['file_url'], headers={"Authorization": f"Bearer {ACCESS_TOKEN}"})
                        temp_path = os.path.join(UPLOAD_FOLDER, f"temp_{remitente}.pdf")
                        with open(temp_path, 'wb') as f:
                            f.write(r.content)
                        texto = extraer_texto_pdf(temp_path)
                        if texto:
                            resumen = resumir_con_gemini(texto)
                            if len(resumen) > 1600:
                                for parte in [resumen[i:i+1600] for i in range(0, len(resumen), 1600)]:
                                    enviar_mensaje_texto(remitente, parte)
                            else:
                                enviar_mensaje_texto(remitente, f"📄 *RESUMEN*\n\n{resumen}")
                        else:
                            enviar_mensaje_texto(remitente, "❌ No se pudo extraer texto del PDF")
                        threading.Thread(target=programar_borrado, args=(temp_path,)).start()
                    elif cuerpo == '3':
                        # Extraer texto
                        r = requests.get(sesion['file_url'], headers={"Authorization": f"Bearer {ACCESS_TOKEN}"})
                        temp_path = os.path.join(UPLOAD_FOLDER, f"temp_{remitente}.pdf")
                        with open(temp_path, 'wb') as f:
                            f.write(r.content)
                        texto = extraer_texto_pdf(temp_path)
                        if texto:
                            if len(texto) > 1500:
                                texto = texto[:1500] + "\n\n[texto truncado]"
                            enviar_mensaje_texto(remitente, f"📄 *TEXTO EXTRAÍDO*\n\n{texto}")
                        else:
                            enviar_mensaje_texto(remitente, "❌ No se pudo extraer texto")
                        threading.Thread(target=programar_borrado, args=(temp_path,)).start()
                    
                    del sesiones[remitente]
                else:
                    enviar_mensaje_texto(remitente, "⚠️ Responde con *1*, *2* o *3*")
                return "OK", 200
            else:
                enviar_mensaje_texto(remitente, MENSAJE_BIENVENIDA)

        # --- SI ES DOCUMENTO ---
        elif 'document' in mensaje:
            doc = mensaje['document']
            filename = doc.get('filename', 'archivo')
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

            # Obtener URL del archivo
            file_data = requests.get(
                f"https://graph.facebook.com/v18.0/{doc['id']}",
                headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}
            ).json()

            if 'url' not in file_data:
                enviar_mensaje_texto(remitente, "❌ No se pudo obtener el archivo")
                return "OK", 200

            # Si es PDF, preguntar qué hacer
            if ext == 'pdf':
                enviar_mensaje_texto(remitente, 
                    "📄 *PDF recibido*\n\n"
                    "¿Qué quieres hacer?\n\n"
                    "📎 *1* → Convertir a Word\n"
                    "🤖 *2* → Resumir con IA\n"
                    "📝 *3* → Extraer texto\n\n"
                    "Responde con el número"
                )
                sesiones[remitente] = {
                    'esperando': 'opcion_ia',
                    'file_url': file_data['url'],
                    'filename': filename
                }
                return "OK", 200

            # Si no es PDF, convertir directamente
            if ext in CONVERSIONES:
                conv = CONVERSIONES[ext][0]
                input_f, output_f, ext_sal, label, mime = conv
                enviar_mensaje_texto(remitente, f"⏳ Convirtiendo a {label}...")
                threading.Thread(target=procesar_y_convertir, args=(
                    file_data['url'], filename, input_f, output_f, ext_sal, mime, remitente
                )).start()
            else:
                enviar_mensaje_texto(remitente, f"⚠️ Formato .{ext} no soportado")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

    return "OK", 200

@app.route('/download/<filename>')
def descargar_archivo(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/')
def home():
    return "🤖 PDFMagic Bot funcionando"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)