"""Núcleo do bot: coleta, filtro, link de afiliado, publicação e histórico."""
import hashlib
import html
import json
import logging
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlencode, urlparse, parse_qsl, urlunparse

import requests

log = logging.getLogger("bot")
UA = {"User-Agent": "Mozilla/5.0 (compatible; OfertasBot/1.0)"}
PRICE_RE = re.compile(r"R\$\s*([\d\.]+,\d{2})")
MEDIA_NS = "{http://search.yahoo.com/mrss/}"


@dataclass
class Offer:
    title: str
    url: str
    store: str
    price: Optional[float] = None
    old_price: Optional[float] = None
    image: Optional[str] = None

    @property
    def uid(self) -> str:
        return hashlib.sha1(self.url.split("?")[0].encode()).hexdigest()

    @property
    def discount(self) -> Optional[int]:
        if self.price and self.old_price and self.old_price > self.price:
            return round((1 - self.price / self.old_price) * 100)
        return None


# ---------- banco ----------
def init_db(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE IF NOT EXISTS posted (
        uid TEXT PRIMARY KEY, title TEXT, store TEXT, price REAL,
        affiliate_url TEXT, posted_at TEXT DEFAULT CURRENT_TIMESTAMP)"""
    )
    db.commit()
    return db


def already_posted(db, offer: Offer) -> bool:
    # 1. Checa por UID da URL
    if db.execute("SELECT 1 FROM posted WHERE uid=?", (offer.uid,)).fetchone():
        return True
    # 2. Checa por Título nos últimos 7 dias para evitar posts duplicados com links levemente diferentes
    clean_title = offer.title.strip().lower()
    row = db.execute("SELECT 1 FROM posted WHERE LOWER(TRIM(title))=? AND posted_at >= datetime('now', '-7 days')", (clean_title,)).fetchone()
    return row is not None


def mark_posted(db, offer: Offer, aff_url: str):
    db.execute(
        "INSERT OR IGNORE INTO posted (uid,title,store,price,affiliate_url) VALUES (?,?,?,?,?)",
        (offer.uid, offer.title, offer.store, offer.price, aff_url),
    )
    db.commit()


# ---------- coleta ----------
def _brl(s: str) -> float:
    # Remove caracteres estranhos (como non-breaking spaces) e formata
    cleaned = re.sub(r"[^\d,\.]", "", s)
    return float(cleaned.replace(".", "").replace(",", "."))


def _store_of(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    return host


def fetch_gatry_amazon() -> list[Offer]:
    """Coleta ofertas em tempo real do Gatry, filtrando apenas as que são da Amazon."""
    from bs4 import BeautifulSoup
    r = requests.get("https://gatry.com/", headers=UA, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    offers = []
    for art in soup.select("article"):
        a_tag = art.select_one("div.image a[href]")
        if not a_tag:
            continue
        link = a_tag["href"]
        if "amazon.com.br" not in link:
            continue  # Filtra APENAS produtos da Amazon!
            
        h3 = art.select_one("h3")
        title = h3.get_text(strip=True) if h3 else "Produto em Oferta"
        
        price_el = art.select_one(".price")
        price = None
        if price_el:
            p_text = price_el.get_text(strip=True)
            try:
                price = _brl(p_text)
            except Exception:
                pass
                
        img_el = art.select_one("img[src]")
        image = img_el["src"] if img_el else None
        
        # Limpa parâmetros antigos da URL e mantém o link limpo da Amazon
        clean_url = link.split("?")[0]
        offers.append(Offer(title=title, url=clean_url, store="amazon.com.br", price=price, old_price=None, image=image))
    return offers


def fetch_feed(feed_url: str) -> list[Offer]:
    """Lê um feed RSS/Atom de promoções. Suporta feeds normais e feeds gerados do Telegram."""
    r = requests.get(feed_url, headers=UA, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    offers = []
    for item in root.iter("item"):
        title = html.unescape(item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = html.unescape(item.findtext("description") or "")
        
        # Se for um feed do Telegram (ex: RSS.app), o link real do produto costuma estar dentro da descrição
        urls_in_desc = re.findall(r'href=[\'"]([^\'"]+)[\'"]', desc)
        if urls_in_desc:
            # Procura por links que não sejam do próprio Telegram
            prod_links = [u for u in urls_in_desc if "t.me" not in u and "telesco.pe" not in u]
            if prod_links:
                link = prod_links[0]

        if not title or not link:
            continue
            
        # Limpa URLs incompletas/cortadas que possam vir embutidas no título pelo RSS
        title = re.sub(r'https?://\S+', '', title).strip()
        title = re.sub(r'\s{2,}', ' ', title)
            
        prices = [_brl(p) for p in PRICE_RE.findall(title + " " + desc)]
        # Se não achou R$ xx,xx mas achou 'por 7199' ou 'por 99'
        if not prices:
            fallback_prices = re.findall(r'por\s+([0-9]+(?:\.[0-9]{3})*(?:,[0-9]{2})?)', desc, re.IGNORECASE)
            for fp in fallback_prices:
                try:
                    prices.append(float(fp.replace(".", "").replace(",", ".")))
                except ValueError:
                    pass

        image = None
        for tag in ("enclosure", f"{MEDIA_NS}thumbnail", f"{MEDIA_NS}content"):
            el = item.find(tag)
            if el is not None and (el.get("url")):
                image = el.get("url")
                break
        if not image:
            m = re.search(r'<img[^>]+src="([^"]+)"', desc)
            image = m.group(1) if m else None
            
        price = min(prices) if prices else None
        old = max(prices) if len(prices) > 1 else None
        offers.append(Offer(title, link, _store_of(link), price, old, image))
    return offers


# ---------- filtros ----------
def passes_filters(o: Offer, cfg: dict) -> bool:
    f = cfg["filters"]
    allowed = [s for s in cfg["affiliate"]]
    if not any(s in o.store for s in allowed):
        return False  # só divulga lojas com programa de afiliados configurado
    if f.get("require_price", True) and not o.price:
        return False
    if o.price and o.price > f.get("max_price", 1e9):
        return False
    if o.price and o.price < f.get("min_price", 0):
        return False
    if o.discount is not None and o.discount < f.get("min_discount", 0):
        return False
    t = o.title.lower()
    if any(k.lower() in t for k in f.get("blocked_keywords", [])):
        return False
    inc = f.get("include_keywords", [])
    if inc and not any(k.lower() in t for k in inc):
        return False
    return True


# ---------- link de afiliado ----------
def resolve_ml_direct_url_and_image(url: str, fallback_image: str = None) -> tuple[str, Optional[str]]:
    """Resolve para o link direto do produto e extrai a imagem real oficial do anúncio (descartando logos/banners)."""
    clean_url = url.split("?")[0]
    real_image = fallback_image
    
    try:
        from curl_cffi import requests as c_requests
        from bs4 import BeautifulSoup
        
        # Usa c_requests (impersonate chrome) para o Mercado Livre não bloquear a requisição
        r = c_requests.get(url, impersonate="chrome120", timeout=12)
        final_url = r.url
        if "/social/" in final_url:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/p/MLB" in href or "/MLB-" in href:
                    final_url = href.split("?")[0]
                    # Abre a página do produto para pegar a foto oficial
                    r = c_requests.get(final_url, impersonate="chrome120", timeout=12)
                    break
        clean_url = final_url.split("?")[0]

        # Extrai a foto oficial de alta definição do catálogo do Mercado Livre
        if r.ok:
            soup_prod = BeautifulSoup(r.text, "html.parser")
            for img in soup_prod.find_all("img"):
                src = img.get("src") or img.get("data-src") or ""
                if "http2.mlstatic.com/D_NQ_NP_" in src and not src.endswith(".gif"):
                    real_image = src
                    break
    except Exception as e:
        log.warning("Erro ao buscar foto oficial do ML: %s", e)

    return clean_url, real_image


def affiliate_url(o: Offer, cfg: dict) -> Optional[str]:
    """Para cada loja, gera o link com a comissão do afiliado."""
    for domain, rule in cfg["affiliate"].items():
        if domain not in o.store and domain not in o.url:
            continue
        # Regra especial para Magazine Luiza (Magazine Você)
        if "magazinevoce" in domain or "magazineluiza" in domain or "magazinevoce.com.br" in o.url:
            store_name = rule.get("store_name", "magazinelucsvendas")
            # Substitui a loja de terceiros (ex: /magazineamarcheu/) pela sua loja (/magazinelucsvendas/)
            converted_url = re.sub(r'magazinevoce\.com\.br/magazine[^/]+', f'magazinevoce.com.br/{store_name}', o.url)
            # Se for um link comum do magazineluiza.com.br, converte para a estrutura do magazinevoce
            if "magazineluiza.com.br" in converted_url:
                converted_url = converted_url.replace("magazineluiza.com.br", f"magazinevoce.com.br/{store_name}")
            return converted_url

        # Regra especial para Shopee
        if "shopee" in domain or "shopee" in o.url or "shope.ee" in o.url:
            # Se for link encurtado, resolve o link real primeiro
            target_url = o.url
            if "s.shopee.com.br" in target_url or "shope.ee" in target_url:
                try:
                    r = requests.get(target_url, allow_redirects=True, headers=UA, timeout=10)
                    target_url = r.url
                except Exception:
                    pass
            p = urlparse(target_url)
            q = dict(parse_qsl(p.query))
            q["utm_source"] = rule.get("utm_source", "an_18344990452")
            q["mmp_pid"] = rule.get("mmp_pid", "an_18344990452")
            q["utm_campaign"] = rule.get("utm_campaign", "id_AHyshKFxj7R")
            q["utm_medium"] = "affiliates"
            return urlunparse(p._replace(query=urlencode(q)))

        # Regra especial para Mercado Livre
        if "mercadolivre" in domain or "mercadolivre" in o.url or "meli.la" in o.url:
            clean_product_url, real_img = resolve_ml_direct_url_and_image(o.url, o.image)
            if real_img:
                o.image = real_img
            p = urlparse(clean_product_url)
            q = dict(parse_qsl(p.query))
            q["matt_tool"] = rule.get("matt_tool", "69788222")
            q["matt_word"] = rule.get("matt_word", "nuneslucas20211001023212")
            return urlunparse(p._replace(query=urlencode(q)))

        if "template" in rule:
            return rule["template"].replace("{url}", o.url).replace("{url_encoded}", quote(o.url, safe=""))
        if "tag_param" in rule:
            p = urlparse(o.url)
            q = dict(parse_qsl(p.query))
            q[rule["tag_param"]] = rule["tag_value"]
            return urlunparse(p._replace(query=urlencode(q)))
    return None


# ---------- Telegram ----------
# ---------- frases descontraídas para o topo ----------
HOOKS = [
    "OLHA ESSE ACHADINHO QUE EU ENCONTREI HOJE",
    "ESSE PREÇO TÁ UMA IGNORÂNCIA DE BOM",
    "VALE CADA CENTAVO, CORRE ANTES QUE ACABE",
    "ESSE AQUI TÁ BARATO DEMAIS PRA SER VERDADE",
    "SE EU NÃO COMPRAR, EU VOU ME ARREPENDER",
    "ACHADO DE OURO PRA VOCÊS HOJE",
    "DESCONTO BRABO PRA QUEM TÁ PROCURANDO ESSE",
    "ESSE PRODUTO É SIMPLESMENTE MARAVILHOSO",
    "QUEM PEGAR PRIMEIRO SE DEU MUITO BEM",
    "PREÇO BAIXO DE VERDADE PRA APROVEITAR",
]

# ---------- encurtador de links ----------
def shorten_url(url: str) -> str:
    """Não usa mais encurtadores de terceiros (TinyURL injetava anúncio + redirecionamento VigLink,
    arriscando a comissão). O link oficial é exibido atrás de um texto curto clicável."""
    return url


def format_message(o: Offer, link: str, cfg: dict) -> str:
    esc = html.escape
    import random
    
    # Seleciona uma frase chamativa baseada no título
    hook = HOOKS[abs(hash(o.title)) % len(HOOKS)]
    
    lines = [
        f"<b>{hook}</b>",
        "",
        f"📦 {esc(o.title)}",
    ]
    
    if o.price:
        val_str = f"{o.price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        if val_str.endswith(",00"):
            val_str = val_str[:-3]
        lines.append(f"💵 <b>R$ {val_str}</b>")
        
    if o.discount:
        lines.append(f"🔥 <i>{o.discount}% de desconto</i>")
        
    lines.append("")
    # Texto curto clicável apontando para o link oficial de afiliado
    lines.append(f'👉 <a href="{esc(link)}">🛒 Comprar aqui</a>')
    
    return "\n".join(lines).strip()


def send_telegram(token: str, chat_id: str, o: Offer, text: str, dry_run: bool) -> bool:
    if dry_run:
        log.info("[DRY-RUN] -> %s\n%s", chat_id, text)
        return True
    base = f"https://api.telegram.org/bot{token}"
    if o.image:
        try:
            # Baixa a imagem e converte para JPEG puro usando Pillow para garantir que o Telegram renderize sempre
            import io
            from PIL import Image
            img_resp = requests.get(o.image, headers=UA, timeout=15)
            if img_resp.ok:
                image = Image.open(io.BytesIO(img_resp.content))
                if image.mode in ("RGBA", "P"):
                    image = image.convert("RGB")
                out_io = io.BytesIO()
                image.save(out_io, format="JPEG", quality=90)
                out_io.seek(0)
                files = {"photo": ("foto.jpg", out_io, "image/jpeg")}
                r_file = requests.post(f"{base}/sendPhoto", data={
                    "chat_id": chat_id, "caption": text, "parse_mode": "HTML"}, files=files, timeout=30)
                if not r_file.ok:
                    # Fallback sem HTML se falhar na formatação
                    r_file = requests.post(f"{base}/sendPhoto", data={
                        "chat_id": chat_id, "caption": text}, files=files, timeout=30)
                if r_file.ok:
                    return True
                else:
                    log.warning("sendPhoto falhou: %s", r_file.text)
        except Exception as e:
            log.warning("Erro ao processar/converter foto para Telegram: %s", e)

    r = requests.post(f"{base}/sendMessage", json={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True}, timeout=30)
    if not r.ok:
        r = requests.post(f"{base}/sendMessage", json={
            "chat_id": chat_id, "text": text,
            "disable_web_page_preview": True}, timeout=30)
    if not r.ok:
        log.error("Telegram erro: %s", r.text[:300])
    return r.ok


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
