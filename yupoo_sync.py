#!/usr/bin/env python3
"""
Sincronizador Automático de Catálogo Yupoo -> Web Propia / WooCommerce
Proveedor configurado: https://nfl-cyq888.x.yupoo.com
Álbum inicial de referencia: 218134333 (Categoría 5214685)

Funciones principales:
1. Extrae álbumes, títulos, descripciones y fotos en alta resolución de Yupoo.
2. Descarga las imágenes enviando el header 'Referer: https://yupoo.com' para evitar el bloqueo 403 Anti-Hotlinking.
3. Verifica qué álbumes siguen activos en el proveedor y marca como 'out_of_stock' (Agotado) los álbumes eliminados.
4. Genera y actualiza 'catalog.json' para el catálogo web y opcionalmente sincroniza vía API REST con WooCommerce.
"""

import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse
import urllib.request
import urllib.error

# ==============================================================================
# CONFIGURACIÓN DE TU TIENDA Y PROVEEDOR
# ==============================================================================
YUPOO_BASE_URL = "https://nfl-cyq888.x.yupoo.com"
TARGET_URLS = [
    "https://nfl-cyq888.x.yupoo.com/albums/218134333?uid=1&isSubCate=false&referrercate=5214685",
    "https://nfl-cyq888.x.yupoo.com/categories/5214685"
]

CATALOG_JSON_PATH = "catalog.json"
IMAGES_DIR = "images"
DEFAULT_PRICE_MXN = 890
DEFAULT_SIZES = ["S", "M", "L", "XL", "XXL"]

# Cabeceras obligatorias para evitar bloqueo 403 de Yupoo al descargar fotos
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://yupoo.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}


def fetch_html(url: str) -> tuple[int, str]:
    """Consulta una URL de Yupoo y devuelve (status_code, html)."""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            status = response.getcode()
            html = response.read().decode("utf-8", errors="ignore")
            return status, html
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        print(f"[WARN] Error de conexión al consultar {url}: {e}")
        return 0, ""


def download_yupoo_image(img_url: str, album_id: str, index: int) -> str:
    """
    Descarga una imagen de photo.yupoo.com enviando Referer: https://yupoo.com/
    y devuelve la ruta local lista para publicarse en tu web o subir a Cloudflare R2 / WooCommerce.
    """
    if img_url.startswith("//"):
        img_url = "https:" + img_url

    os.makedirs(os.path.join(IMAGES_DIR, album_id), exist_ok=True)
    ext = os.path.splitext(urlparse(img_url).path)[1] or ".jpg"
    local_path = os.path.join(IMAGES_DIR, album_id, f"foto_{index}{ext}")

    if os.path.exists(local_path) and os.path.getsize(local_path) > 1000:
        return local_path

    req = urllib.request.Request(img_url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            with open(local_path, "wb") as f:
                f.write(resp.read())
        print(f"  [OK] Imagen guardada: {local_path}")
        return local_path
    except Exception as e:
        print(f"  [WARN] No se pudo descargar imagen {img_url}: {e}")
        return img_url


def parse_album_page(album_url: str, album_id: str) -> dict | None:
    """Extrae título, descripción y galería de imágenes de un álbum de Yupoo."""
    status, html = fetch_html(album_url)
    if status == 404 or not html:
        return None

    # Extraer título del álbum
    title_match = re.search(
        r'<span class="showalbumheader__gallerytitle"[^>]*>(.*?)</span>',
        html,
        re.DOTALL,
    )
    if not title_match:
        title_match = re.search(r"<title>(.*?)</title>", html, re.DOTALL)

    raw_title = title_match.group(1).strip() if title_match else f"Jersey Álbum #{album_id}"
    clean_title = re.sub(r"\s*\|\s*Yupoo.*$", "", raw_title, flags=re.IGNORECASE).strip()

    # Extraer descripción si existe
    desc_match = re.search(
        r'<div class="showalbumheader__gallerysubtitle[^"]*"[^>]*>(.*?)</div>',
        html,
        re.DOTALL,
    )
    description = (
        re.sub(r"<[^>]+>", "", desc_match.group(1)).strip()
        if desc_match
        else "Jersey edición especial importado. Costuras bordadas y tela transpirable."
    )

    # Extraer URLs de fotos de alta resolución (data-origin-src en Yupoo)
    img_matches = re.findall(r'data-origin-src=["\']([^"\']+photo\.yupoo\.com[^"\']+)["\']', html)
    if not img_matches:
        img_matches = re.findall(r'src=["\']([^"\']+photo\.yupoo\.com[^"\']+)["\']', html)

    # Eliminar duplicados preservando orden
    seen = set()
    unique_imgs = []
    for img in img_matches:
        if img not in seen:
            seen.add(img)
            unique_imgs.append(img)

    local_images = []
    for idx, img_url in enumerate(unique_imgs[:6], start=1):
        saved_path = download_yupoo_image(img_url, album_id, idx)
        local_images.append(saved_path)

    return {
        "id": f"YP-{album_id}",
        "albumId": str(album_id),
        "title": clean_title,
        "category": "NFL",
        "price": DEFAULT_PRICE_MXN,
        "sizes": DEFAULT_SIZES,
        "inStock": True,
        "description": description,
        "yupooUrl": album_url,
        "images": local_images,
        "lastChecked": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def extract_album_links_from_category(cat_url: str) -> list[tuple[str, str]]:
    """Recorre una página de categoría de Yupoo y devuelve lista de (album_id, album_url)."""
    status, html = fetch_html(cat_url)
    if status != 200 or not html:
        return []

    matches = re.findall(r'href=["\'](/albums/(\d+)[^"\']*)["\']', html)
    results = []
    seen = set()
    for rel_url, alb_id in matches:
        if alb_id not in seen:
            seen.add(alb_id)
            full_url = urljoin(YUPOO_BASE_URL, rel_url)
            results.append((alb_id, full_url))
    return results


def sync_catalog():
    """
    Flujo principal de sincronización:
    1. Carga el catálogo existente (catalog.json).
    2. Revisa cada producto existente en Yupoo: si el álbum fue borrado (404), lo marca Agotado (inStock = False).
    3. Descubre nuevos álbumes desde TARGET_URLS y los agrega al catálogo.
    4. Guarda el archivo catalog.json actualizado.
    """
    print("=== INICIANDO SINCRONIZACIÓN DE EXISTENCIAS CON YUPOO ===")
    existing_items = {}
    if os.path.exists(CATALOG_JSON_PATH):
        try:
            with open(CATALOG_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    existing_items[str(item.get("albumId"))] = item
        except Exception as e:
            print(f"[WARN] No se pudo leer {CATALOG_JSON_PATH}: {e}")

    # 1. Verificar álbumes en las URLs configuradas
    discovered_albums = {}
    for url in TARGET_URLS:
        alb_match = re.search(r"/albums/(\d+)", url)
        if alb_match:
            discovered_albums[alb_match.group(1)] = url
        elif "/categories/" in url:
            print(f"Explorando categoría: {url}")
            for alb_id, alb_url in extract_album_links_from_category(url):
                discovered_albums[alb_id] = alb_url

    # 2. Actualizar productos existentes (Control de Existencias)
    for alb_id, item in existing_items.items():
        check_url = item.get("yupooUrl") or f"{YUPOO_BASE_URL}/albums/{alb_id}"
        print(f"Verificando vigencia del álbum #{alb_id}...")
        status, _ = fetch_html(check_url)
        if status == 404:
            print(f"  -> [AGOTADO] El álbum #{alb_id} fue retirado por el proveedor.")
            item["inStock"] = False
        elif status == 200:
            print(f"  -> [DISPONIBLE] Álbum #{alb_id} activo.")
            item["inStock"] = True
        item["lastChecked"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        time.sleep(0.8)

    # 3. Agregar álbumes nuevos
    for alb_id, alb_url in discovered_albums.items():
        if alb_id not in existing_items:
            print(f"Nuevo álbum detectado #{alb_id}. Extrayendo datos y fotos...")
            parsed = parse_album_page(alb_url, alb_id)
            if parsed:
                existing_items[alb_id] = parsed
            time.sleep(1.0)

    # 4. Guardar catálogo actualizado
    final_list = list(existing_items.values())
    with open(CATALOG_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(final_list, f, ensure_ascii=False, indent=2)

    print(f"=== SINCRONIZACIÓN COMPLETADA: {len(final_list)} productos en {CATALOG_JSON_PATH} ===")


if __name__ == "__main__":
    sync_catalog()
