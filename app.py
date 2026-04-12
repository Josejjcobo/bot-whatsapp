def procesar_y_convertir(file_url, nombre_original, telefono):
    """Descarga de Meta, convierte en CloudConvert y programa limpieza"""
    try:
        # 1. Descargar el archivo desde los servidores de Meta
        r = requests.get(file_url, headers={"Authorization": f"Bearer {ACCESS_TOKEN}"})
        input_path = os.path.join(UPLOAD_FOLDER, nombre_original)
        with open(input_path, 'wb') as f:
            f.write(r.content)
        
        print(f"📥 Archivo descargado: {nombre_original}")
        
        # 2. Configurar headers para CloudConvert API v2
        api_key = CC_API_KEY
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # 3. Crear el job con las tareas
        # NOTA: CloudConvert API v2 requiere que las tareas estén correctamente referenciadas
        # con el parámetro "input" que es un array de nombres de tareas [citation:9][citation:10]
        job_data = {
            "tasks": {
                "import-file": {
                    "operation": "import/upload"
                },
                "convert-file": {
                    "operation": "convert",
                    "input": ["import-file"],  # <-- IMPORTANTE: debe ser un array
                    "input_format": "docx",
                    "output_format": "pdf"
                },
                "export-file": {
                    "operation": "export/url",
                    "input": ["convert-file"]  # <-- IMPORTANTE: debe ser un array
                }
            }
        }
        
        print("📦 Creando job en CloudConvert...")
        response = requests.post(
            "https://api.cloudconvert.com/v2/jobs",
            json=job_data,
            headers=headers
        )
        
        if response.status_code != 201:
            print(f"❌ Error al crear job: {response.text}")
            raise Exception(f"Error al crear job: {response.status_code}")
        
        job = response.json()
        print(f"✅ Job creado: {job.get('id')}")
        
        # 4. Obtener la URL de subida de la tarea import-file
        upload_url = None
        for task in job.get('tasks', []):
            if task.get('operation') == 'import/upload':
                # La URL puede estar en result.form.url o directamente en result.url
                if 'result' in task and task['result']:
                    if 'form' in task['result'] and 'url' in task['result']['form']:
                        upload_url = task['result']['form']['url']
                    elif 'url' in task['result']:
                        upload_url = task['result']['url']
                break
        
        if not upload_url:
            print(f"❌ Respuesta del job: {job}")
            raise Exception("No se pudo obtener la URL de subida")
        
        print(f"📤 URL de subida obtenida")
        
        # 5. Subir el archivo con PUT a la URL obtenida
        with open(input_path, 'rb') as f:
            upload_response = requests.put(
                upload_url,
                data=f.read(),
                headers={"Content-Type": "application/octet-stream"}
            )
        
        if upload_response.status_code not in [200, 201, 204]:
            print(f"❌ Error al subir archivo: {upload_response.text}")
            raise Exception(f"Error al subir archivo: {upload_response.status_code}")
        
        print("📤 Archivo subido a CloudConvert")
        
        # 6. Esperar a que termine la conversión
        print("🔄 Convirtiendo archivo...")
        job_id = job['id']
        
        max_attempts = 60  # 2 minutos máximo (2 segundos * 60)
        for i in range(max_attempts):
            response = requests.get(
                f"https://api.cloudconvert.com/v2/jobs/{job_id}",
                headers=headers
            )
            job_status = response.json()
            
            # Buscar la tarea de exportación
            for task in job_status.get('tasks', []):
                if task.get('operation') == 'export/url' and task.get('status') == 'finished':
                    # Obtener la URL del PDF
                    files = task.get('result', {}).get('files', [])
                    if files and 'url' in files[0]:
                        pdf_url = files[0]['url']
                        
                        # Descargar el PDF
                        pdf_filename = nombre_original.rsplit('.', 1)[0] + ".pdf"
                        pdf_path = os.path.join(UPLOAD_FOLDER, pdf_filename)
                        
                        pdf_response = requests.get(pdf_url)
                        with open(pdf_path, 'wb') as f:
                            f.write(pdf_response.content)
                        
                        print(f"✅ Conversión completada: {pdf_filename}")
                        
                        # Generar link de descarga
                        link = f"{BASE_URL}/download/{pdf_filename}"
                        enviar_mensaje_texto(telefono, f"✅ ¡Conversión lista!\n📄 {pdf_filename}\n🔗 {link}\n⏰ El link expirará en 5 minutos")
                        
                        # Lanzar hilos de borrado
                        threading.Thread(target=programar_borrado, args=(input_path,)).start()
                        threading.Thread(target=programar_borrado, args=(pdf_path,)).start()
                        return
            
            time.sleep(2)
        
        raise Exception("Tiempo de espera agotado para la conversión")

    except Exception as e:
        print(f"❌ Error en conversión: {e}")
        enviar_mensaje_texto(telefono, f"❌ Error: {str(e)}")