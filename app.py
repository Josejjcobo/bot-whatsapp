import os
import time
import threading
import unicodedata
import requests
from flask import Flask, request, send_from_directory

try:
    import google.generativeai as genai
    GEMINI_DISPONIBLE = True
except ImportError:
    GEMINI_DISPONIBLE = False
    print("⚠️ Gemini no disponible. Instala: pip install google-generativeai")

app = Flask(__name__)

# --- CONFIGURACIÓN DE VARIABLES ---
ACCESS_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
CC_API_KEY = os.environ.get("CLOUD_CONVERT_API_KEY")
BASE_URL = os.environ.get("BASE_URL", "https://bot-whatsapp-zcek.onrender.com")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- CONFIGURAR GEMINI (con modelo gemini-pro compatible) ---
gemini_model = None
if GEMINI_API_KEY and GEMINI_DISPONIBLE:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        # Usar gemini-pro que es más compatible
        gemini_model = genai.GenerativeModel('gemini-pro')
        print("🤖 Gemini IA configurada con modelo gemini-pro")
        
        # Prueba rápida para verificar que funciona
        test_response = gemini_model.generate_content("Hola, prueba de conexión")
        print("✅ Gemini funciona correctamente")
    except Exception as e:
        print(f"⚠️ Error configurando Gemini: {e}")
else:
    print("⚠️ GEMINI_API_KEY no configurada o gemini no disponible")

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
    "jpg": [("jpg", "pdf", ".pdf", "PDF", "image/jpeg")],
    "jpeg": [("jpeg", "pdf", ".pdf", "PDF", "image/jpeg")],
    "png": [("png", "pdf", ".pdf", "PDF", "image/png")],
    "pdf": [("pdf", "docx", ".docx", "Word (DOCX)", "application/pdf")],
}

MENSAJE_BIENVENIDA = (
    "🤖 *¡Hola! Soy PDFMagic Bot*\n\n"
    "Envía un archivo y te preguntaré qué quieres hacer con él.\n\n"
    "📄 *DOCX* → Convertir a PDF\n"
    "📊 *XLSX* → Convertir a PDF\n"
    "🖼️ *JPG/PNG* → Convertir a PDF\n"
    "📑 *PDF* → Convertir a Word / Resumir / Traducir\n\n"
    "¡Envía tu archivo!"
)

def sanitizar_nombre(nombre):
    """Elimina acentos y reemplaza espacios por guiones bajos."""
    n = unicodedata.normalize('NFKD', nombre)
    n = n.encode('ascii', 'ignore').decode('ascii')
    return n.replace(' ', '_')

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

def extraer_texto_pdf(pdf_path):
    """Extrae texto de un archivo PDF usando PyPDF2."""
    try:
        import PyPDF2
        texto = ""
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    texto += page_text + "\n"
        return texto if texto.strip() else None
    except Exception as e:
        print(f"❌ Error extrayendo texto del PDF: {e}")
        return None

def resumir_con_gemini(texto):
    """Genera un resumen del texto usando Gemini IA."""
    if not gemini_model:
        return "❌ Servicio de IA no disponible. Contacta al administrador."
    
    if not texto or len(texto.strip()) < 50:
        return "⚠️ El documento tiene muy poco texto para generar un resumen."
    
    # Limitar texto a 8000 caracteres para no exceder tokens
    if len(texto) > 8000:
        texto = texto[:8000] + "\n[...texto truncado...]"
    
    prompt = f"""
    Eres un asistente experto en análisis de documentos. Tu tarea es resumir el siguiente texto de manera clara, concisa y profesional.

    INSTRUCCIONES:
    1. Identifica los puntos más importantes del documento
    2. Estructura el resumen con viñetas o párrafos cortos
    3. Incluye datos clave, fechas, nombres o cifras relevantes
    4. Mantén un tono neutral y objetivo

    TEXTO A RESUMIR:
    {texto}

    RESUMEN:
    """
    
    try:
        respuesta = gemini_model.generate_content(prompt)
        resumen = respuesta.text
        
        # Limitar resumen a 2000 caracteres para WhatsApp
        if len(resumen) > 2000:
            resumen = resumen[:2000] + "\n\n[...resumen truncado...]"
        
        return f"📄 *RESUMEN DEL DOCUMENTO*\n\n{resumen}\n\n---\n✨ Generado con Google Gemini IA"
    except Exception as e:
        print(f"❌ Error con Gemini: {e}")
        return f"❌ Error al generar resumen: {str(e)[:100]}"

def traducir_con_gemini(texto, idioma):
    """Traduce el texto al idioma seleccionado usando Gemini IA."""
    if not gemini_model:
        return "❌ Servicio de IA no disponible. Contacta al administrador."
    
    if not texto or len(texto.strip()) < 10:
        return "⚠️ El documento no tiene suficiente texto para traducir."
    
    # Limitar texto a 8000 caracteres
    if len(texto) > 8000:
        texto = texto[:8000] + "\n[...texto truncado...]"
    
    idiomas = {'1': 'inglés', '2': 'francés'}
    lang = idiomas.get(idioma, 'inglés')
    
    prompt = f"""
    Traduce el siguiente texto al {lang} de forma natural, precisa y manteniendo el formato original.
    
    TEXTO ORIGINAL:
    {texto}
    
    TRADUCCIÓN AL {lang.upper()}:
    """
    
    try:
        respuesta = gemini_model.generate_content(prompt)
        traduccion = respuesta.text
        
        if len(traduccion) > 2000:
            traduccion = traduccion[:2000] + "\n\n[...traducción truncada...]"
        
        idioma_nombre = "INGLÉS" if idioma == '1' else "FRANCÉS"
        return f"🌐 *TRADUCCIÓN A {idioma_nombre}*\n\n{traduccion}\n\n---\n✨ Generado con Google Gemini IA"
    except Exception as e:
        print(f"❌ Error con Gemini: {e}")
        return f"❌ Error al traducir: {str(e)[:100]}"

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
        entry = data['entry'][0]['changes'][0]['value']
        if 'messages' not in entry:
            return "OK", 200

        mensaje = entry['messages'][0]
        remitente = mensaje['from']
        print(f"📱 De: {remitente}")

        # ── MENSAJE DE TEXTO ──────────────────────────────
        if 'text' in mensaje:
            cuerpo = mensaje['text']['body'].strip()
            print(f"💬 Texto: {cuerpo}")

            # Verificar si el usuario está en una sesión activa
            if remitente in sesiones:
                sesion = sesiones[remitente]
                estado = sesion.get('estado', '')

                # Esperando opción para archivo que NO es PDF
                if estado == 'esperando_opcion_normal':
                    if cuerpo == '1':
                        enviar_mensaje_texto(remitente, "⏳ Convirtiendo a PDF... 🔄")
                        threading.Thread(target=procesar_y_convertir, args=(
                            sesion['file_url'], sesion['filename'], 
                            sesion['input_format'], 'pdf', '.pdf', 
                            sesion['mime_type'], remitente
                        )).start()
                        del sesiones[remitente]
                    else:
                        enviar_mensaje_texto(remitente, "⚠️ Responde *1* para convertir a PDF")
                    return "OK", 200

                # Esperando opción para PDF
                elif estado == 'esperando_opcion_pdf':
                    if cuerpo == '1':
                        enviar_mensaje_texto(remitente, "⏳ Convirtiendo a Word... 🔄")
                        threading.Thread(target=procesar_y_convertir, args=(
                            sesion['file_url'], sesion['filename'], 
                            'pdf', 'docx', '.docx', 'application/pdf', remitente
                        )).start()
                        del sesiones[remitente]
                    elif cuerpo == '2':
                        enviar_mensaje_texto(remitente, "📝 *Resumen del documento*\n\n⏳ Procesando...")
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
                                enviar_mensaje_texto(remitente, resumen)
                        else:
                            enviar_mensaje_texto(remitente, "❌ No se pudo extraer texto del PDF. ¿Es un documento escaneado?")
                        threading.Thread(target=programar_borrado, args=(temp_path,)).start()
                        del sesiones[remitente]
                    elif cuerpo == '3':
                        enviar_mensaje_texto(remitente, "🌐 *Traducción*\n\n¿A qué idioma?\n\n🇬🇧 *1* → Inglés\n🇫🇷 *2* → Francés")
                        sesion['estado'] = 'esperando_idioma'
                    else:
                        enviar_mensaje_texto(remitente, "⚠️ Opciones:\n*1* → Word\n*2* → Resumen\n*3* → Traducir")
                    return "OK", 200

                # Esperando idioma para traducción
                elif estado == 'esperando_idioma':
                    if cuerpo in ['1', '2']:
                        enviar_mensaje_texto(remitente, "🌐 *Traduciendo documento...* ⏳")
                        r = requests.get(sesion['file_url'], headers={"Authorization": f"Bearer {ACCESS_TOKEN}"})
                        temp_path = os.path.join(UPLOAD_FOLDER, f"temp_{remitente}.pdf")
                        with open(temp_path, 'wb') as f:
                            f.write(r.content)
                        texto = extraer_texto_pdf(temp_path)
                        if texto:
                            traduccion = traducir_con_gemini(texto, cuerpo)
                            if len(traduccion) > 1600:
                                for parte in [traduccion[i:i+1600] for i in range(0, len(traduccion), 1600)]:
                                    enviar_mensaje_texto(remitente, parte)
                            else:
                                enviar_mensaje_texto(remitente, traduccion)
                        else:
                            enviar_mensaje_texto(remitente, "❌ No se pudo extraer texto del PDF para traducir")
                        threading.Thread(target=programar_borrado, args=(temp_path,)).start()
                        del sesiones[remitente]
                    else:
                        enviar_mensaje_texto(remitente, "⚠️ Responde *1* (Inglés) o *2* (Francés)")
                    return "OK", 200
                else:
                    enviar_mensaje_texto(remitente, MENSAJE_BIENVENIDA)
            else:
                enviar_mensaje_texto(remitente, MENSAJE_BIENVENIDA)

        # ── DOCUMENTO O IMAGEN ────────────────────────────
        elif 'document' in mensaje or 'image' in mensaje:
            es_imagen = 'image' in mensaje
            if es_imagen:
                media = mensaje['image']
                filename = f"imagen_{media['id']}.jpg"
                ext = 'jpg'
                mime_type = 'image/jpeg'
            else:
                media = mensaje['document']
                filename = media.get('filename', 'archivo')
                ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
                # Determinar MIME type según extensión
                if ext == 'docx':
                    mime_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                elif ext == 'xlsx':
                    mime_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                elif ext == 'pdf':
                    mime_type = 'application/pdf'
                elif ext in ['jpg', 'jpeg']:
                    mime_type = 'image/jpeg'
                elif ext == 'png':
                    mime_type = 'image/png'
                else:
                    mime_type = 'application/octet-stream'

            print(f"📄 Archivo: {filename} (ext: {ext})")

            # Obtener URL del archivo desde Meta
            file_data = requests.get(
                f"https://graph.facebook.com/v18.0/{media['id']}",
                headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}
            ).json()

            if 'url' not in file_data:
                print(f"❌ Meta no devolvió URL: {file_data}")
                enviar_mensaje_texto(remitente, "❌ No se pudo obtener el archivo. Intenta de nuevo.")
                return "OK", 200

            # --- CASO 1: ARCHIVO PDF (con IA) ---
            if ext == 'pdf' and gemini_model:
                enviar_mensaje_texto(remitente,
                    "📄 *PDF recibido*\n\n"
                    "¿Qué quieres hacer?\n\n"
                    "📎 *1* → Convertir a Word\n"
                    "🤖 *2* → Resumir con IA\n"
                    "🌐 *3* → Traducir (Inglés/Francés)\n\n"
                    "Responde con el número de tu elección.")
                sesiones[remitente] = {
                    'estado': 'esperando_opcion_pdf',
                    'file_url': file_data['url'],
                    'filename': filename
                }

            # --- CASO 2: ARCHIVO PDF (sin IA) ---
            elif ext == 'pdf' and not gemini_model:
                enviar_mensaje_texto(remitente,
                    "📄 *PDF recibido*\n\n"
                    "¿Qué quieres hacer?\n\n"
                    "📎 *1* → Convertir a Word\n\n"
                    "Responde con el número.")
                sesiones[remitente] = {
                    'estado': 'esperando_opcion_pdf',
                    'file_url': file_data['url'],
                    'filename': filename
                }

            # --- CASO 3: OTROS FORMATOS (DOCX, XLSX, JPG, PNG) ---
            elif ext in ['docx', 'xlsx', 'jpg', 'jpeg', 'png']:
                enviar_mensaje_texto(remitente,
                    f"📂 *Archivo recibido:* {filename}\n\n"
                    "¿Qué quieres hacer?\n\n"
                    "📎 *1* → Convertir a PDF\n\n"
                    "Responde con el número.")
                sesiones[remitente] = {
                    'estado': 'esperando_opcion_normal',
                    'file_url': file_data['url'],
                    'filename': filename,
                    'input_format': ext,
                    'mime_type': mime_type
                }

            else:
                enviar_mensaje_texto(remitente, 
                    f"⚠️ El formato *.{ext}* no está soportado.\n\n"
                    "Formatos aceptados:\n"
                    "📄 DOCX, XLSX, PDF\n"
                    "🖼️ JPG, JPEG, PNG")

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
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

@app.route('/')
def home():
    return "🤖 PDFMagic Bot con IA funcionando. Webhook en /webhook"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)