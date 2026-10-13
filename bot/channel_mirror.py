import hashlib
import logging
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import requests
from bs4 import BeautifulSoup

log = logging.getLogger("bot")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch_telegram_channel_direct(channel_username: str) -> list[dict]:
    """Coleta as últimas mensagens publicadas diretamente em t.me/s/canal sem delay de RSS."""
    url = f"https://t.me/s/{channel_username}"
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if not r.ok:
            return []
    except Exception as e:
        log.warning("Erro ao acessar t.me/s/%s: %s", channel_username, e)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for wrap in soup.select(".tgme_widget_message_wrap"):
        text_el = wrap.select_one(".tgme_widget_message_text")
        if not text_el:
            continue
        text = text_el.get_text("\n", strip=True)
        # Extrai os links do texto da mensagem
        links = []
        for a in wrap.select(".tgme_widget_message_text a[href]"):
            href = a["href"]
            if "t.me" not in href and "telesco.pe" not in href and "lobaodaspromocoes.com" not in href:
                links.append(href)
        if not links:
            continue
        
        photo_url = None
        photo_el = wrap.select_one(".tgme_widget_message_photo_wrap")
        if photo_el and photo_el.get("style"):
            m = re.search(r"url\(['\"]?([^'\"]+)", photo_el["style"])
            if m:
                photo_url = m.group(1).rstrip("')")
        
        items.append({
            "text": text,
            "link": links[0],
            "image": photo_url
        })
    return items

def convert_to_user_affiliate(link: str, cfg: dict) -> tuple[str, str | None]:
    """Converte qualquer link do concorrente (ML, Shopee, Amazon, Magalu) para a sua comissão."""
    aff = cfg.get("affiliate", {})

    # 1. Mercado Livre (meli.la ou mercadolivre.com.br)
    if "mercadolivre" in link or "meli.la" in link:
        rule = aff.get("mercadolivre.com.br", {})
        matt_tool = rule.get("matt_tool", "69788222")
        matt_word = rule.get("matt_word", "nuneslucas20211001023212")
        
        target_url = link
        real_image = None
        try:
            r = requests.get(link, headers=UA, timeout=12, allow_redirects=True)
            target_url = r.url
            if "/social/" in target_url:
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "/p/MLB" in href or "/MLB-" in href:
                        target_url = href.split("?")[0]
                        break
            soup_prod = BeautifulSoup(r.text, "html.parser")
            for img in soup_prod.find_all("img"):
                src = img.get("src") or img.get("data-src") or ""
                if "http2.mlstatic.com/D_NQ_NP_" in src and not src.endswith(".gif"):
                    real_image = src
                    break
        except Exception:
            pass

        clean_prod = target_url.split("?")[0]
        p = urlparse(clean_prod)
        q = dict(parse_qsl(p.query))
        q["matt_tool"] = matt_tool
        q["matt_word"] = matt_word
        return urlunparse(p._replace(query=urlencode(q))), real_image

    # 2. Amazon (amzn.to, amzlink.to, amazon.com.br)
    if "amazon" in link or "amzn.to" in link or "amzlink.to" in link:
        rule = aff.get("amazon.com.br", {})
        tag = rule.get("tag_value", "promofire0c-20")
        target_url = link
        try:
            r = requests.get(link, headers=UA, timeout=12, allow_redirects=True)
            target_url = r.url
        except Exception:
            pass
        clean_prod = target_url.split("?")[0]
        p = urlparse(clean_prod)
        q = dict(parse_qsl(p.query))
        q["tag"] = tag
        return urlunparse(p._replace(query=urlencode(q))), None

    # 3. Shopee (shopee.com.br, s.shopee.com.br, shope.ee)
    if "shopee" in link or "shope.ee" in link:
        rule = aff.get("shopee.com.br", {})
        target_url = link
        try:
            r = requests.get(link, headers=UA, timeout=12, allow_redirects=True)
            target_url = r.url
        except Exception:
            pass
        p = urlparse(target_url)
        q = dict(parse_qsl(p.query))
        q["utm_source"] = rule.get("utm_source", "an_18344990452")
        q["mmp_pid"] = rule.get("mmp_pid", "an_18344990452")
        q["utm_campaign"] = rule.get("utm_campaign", "id_AHyshKFxj7R")
        q["utm_medium"] = "affiliates"
        return urlunparse(p._replace(query=urlencode(q))), None

    # 4. Magazine Luiza (magazineluiza.com.br, magazinevoce.com.br)
    if "magazineluiza" in link or "magazinevoce" in link:
        rule = aff.get("magazinevoce.com.br", {})
        store_name = rule.get("store_name", "magazinelucsvendas")
        converted_url = re.sub(r"magazinevoce\.com\.br/magazine[^/]+", f"magazinevoce.com.br/{store_name}", link)
        if "magazineluiza.com.br" in converted_url:
            converted_url = converted_url.replace("magazineluiza.com.br", f"magazinevoce.com.br/{store_name}")
        return converted_url, None

    return None, None

def process_and_mirror_channel(channel_username: str, cfg: dict, db, token: str, dry_run: bool) -> int:
    """Captura exatamente as ofertas do canal, troca os links pelos seus e posta no seu grupo."""
    items = fetch_telegram_channel_direct(channel_username)
    if not items:
        return 0
    
    sent = 0
    max_posts = cfg.get("schedule", {}).get("max_posts_per_cycle", 10)
    delay_sec = cfg.get("schedule", {}).get("delay_between_posts_sec", 2)
    
    # Processa do mais antigo para o mais recente para manter a ordem cronológica
    for it in reversed(items):
        if sent >= max_posts:
            break
            
        orig_link = it["link"]
        orig_text = it["text"]
        image = it["image"]
        
        # Identificador único pelo link do produto original
        uid = hashlib.sha1(orig_link.encode()).hexdigest()
        
        # Checa se já postou
        if db.execute("SELECT 1 FROM posted WHERE uid=?", (uid,)).fetchone():
            continue
            
        # Converte o link para o do usuário
        my_link, high_res_image = convert_to_user_affiliate(orig_link, cfg)
        if not my_link:
            continue
            
        final_image = high_res_image or image
        
        # Substitui o link do concorrente pelo seu link de afiliado no texto original
        final_text = orig_text.replace(orig_link, my_link)
        
        from bot.core import Offer, send_telegram
        dummy_offer = Offer(
            title=orig_text.split("\n")[0][:80],
            url=my_link,
            store="espelho",
            image=final_image
        )
        
        ok = True
        for chat in cfg.get("telegram_chats", []):
            ok = send_telegram(token, chat, dummy_offer, final_text, dry_run) and ok
            import time
            time.sleep(delay_sec)
            
        if ok:
            if not dry_run:
                db.execute(
                    "INSERT OR IGNORE INTO posted (uid, title, store, price, affiliate_url) VALUES (?, ?, ?, ?, ?)",
                    (uid, orig_text.split("\n")[0][:150], "espelho", 0.0, my_link)
                )
                db.commit()
            sent += 1
            log.info("Oferta espelhada com sucesso do canal @%s: %s", channel_username, my_link)
            
    return sent
