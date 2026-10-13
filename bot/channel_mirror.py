import hashlib
import logging
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import requests
from bs4 import BeautifulSoup

log = logging.getLogger("bot")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _is_valid_product_photo(url: str, min_side: int = 150, min_max_side: int = 300) -> bool:
    """Baixa a imagem e só aceita se abrir corretamente e tiver tamanho de foto de produto.
    Avatares/logos de perfil minúsculos (ex.: 120x120) são rejeitados, mas fotos verticais
    de produtos (ex: perfumes 270x500) são aceitas normalmente."""
    try:
        import io
        from PIL import Image
        resp = requests.get(url, headers=UA, timeout=12)
        if not resp.ok:
            return False
        w, h = Image.open(io.BytesIO(resp.content)).size
        # Rejeita avatares pequenos (120x120), mas aceita produtos reais
        return max(w, h) >= min_max_side and min(w, h) >= min_side
    except Exception:
        return False

def _clean_words(t: str) -> set[str]:
    """Extrai palavras-chave essenciais para comparação de produtos entre grupos diferentes."""
    words = re.findall(r'[a-zA-Z0-9]+', t.lower())
    stop = {'de', 'do', 'da', 'em', 'com', 'para', 'o', 'a', 'os', 'as', 'e', 'no', 'na', 'por', 'pra', 'dos', 'das'}
    res = set()
    for w in words:
        if w in stop or len(w) <= 2:
            continue
        if w.endswith('s') and len(w) > 3:
            w = w[:-1]
        res.add(w)
    return res

def _is_similar_product(t1: str, t2: str) -> bool:
    """Verifica se dois títulos se referem ao mesmo produto mesmo que escritos de forma ligeiramente diferente."""
    w1, w2 = _clean_words(t1), _clean_words(t2)
    if not w1 or not w2:
        return False
    inter = w1 & w2
    return (len(inter) / min(len(w1), len(w2))) >= 0.75

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
            # A foto oficial do produto fica no og:image. A primeira <img> da página
            # é o AVATAR do perfil de afiliado do concorrente (alt="Logo lobão..."), nunca usar.
            og = soup_prod.find("meta", attrs={"property": "og:image"})
            if og and "mlstatic.com" in (og.get("content") or ""):
                real_image = og["content"]
        except Exception:
            pass

        clean_prod = target_url.split("?")[0]
        p = urlparse(clean_prod)
        q = dict(parse_qsl(p.query))
        q["matt_tool"] = matt_tool
        q["matt_word"] = matt_word
        full_ml = urlunparse(p._replace(query=urlencode(q)))
        # Sem encurtadores de terceiros (TinyURL injeta anúncio + VigLink e pode desviar comissão)
        return full_ml, real_image

    # 2. Amazon (amzn.to, amzlink.to, amazon.com.br, amzn.divulgador.link, etc.)
    if "amazon" in link or "amzn" in link or "amzlink.to" in link:
        rule = aff.get("amazon.com.br", {})
        tag = rule.get("tag_value", "promofire0c-20")
        target_url = link
        try:
            r = requests.get(link, headers=UA, timeout=12, allow_redirects=True)
            target_url = r.url
        except Exception:
            pass
        if "amazon" in target_url:
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
        uid = hashlib.sha1(orig_link.split("?")[0].encode()).hexdigest()
        
        # Checa se já postou esse link exato
        if db.execute("SELECT 1 FROM posted WHERE uid=?", (uid,)).fetchone():
            continue
            
        # Extrai o nome real do produto para evitar postar o mesmo produto duas vezes
        lines = [l.strip() for l in orig_text.split("\n") if l.strip()]
        prod_title = ""
        for l in lines[1:]:
            cleaned = re.sub(r"^[^\w\d]+", "", l).strip()
            if len(cleaned) > 10 and not cleaned.lower().startswith("de r$") and not cleaned.lower().startswith("por:"):
                prod_title = cleaned
                break
        if not prod_title and lines:
            prod_title = lines[0]

        # Checa se o mesmo produto já foi divulgado nos últimos 7 dias (exato ou similar entre grupos diferentes)
        if prod_title:
            # 1. Checagem exata rápida no banco
            row = db.execute(
                "SELECT 1 FROM posted WHERE LOWER(TRIM(title))=? AND posted_at >= datetime('now', '-7 days')",
                (prod_title.lower().strip(),)
            ).fetchone()
            if row:
                log.info("Produto já postado recentemente ignorado (exato): %s", prod_title)
                continue

            # 2. Checagem inteligente de similaridade (pega produtos postados por outros canais concorrentes com títulos parecidos)
            recent_titles = [r[0] for r in db.execute(
                "SELECT title FROM posted WHERE posted_at >= datetime('now', '-7 days') LIMIT 150"
            ).fetchall() if r[0]]
            is_dup = False
            for past_t in recent_titles:
                if _is_similar_product(prod_title, past_t):
                    log.info("Produto ignorado por alta similaridade com '%s': %s", past_t, prod_title)
                    is_dup = True
                    break
            if is_dup:
                continue

        # Converte o link para o do usuário
        my_link, high_res_image = convert_to_user_affiliate(orig_link, cfg)
        if not my_link:
            continue
            
        final_image = None
        for candidate in (image, high_res_image):
            if candidate and _is_valid_product_photo(candidate):
                final_image = candidate
                break
        if not final_image:
            log.info("Ignorando postagem sem foto válida do produto: %s", prod_title)
            continue
        
        # Substitui o link do concorrente por um texto curto clicável que aponta para o SEU link oficial
        import html as _html
        if "mercadolivre" in my_link:
            label = "🛒 Comprar no Mercado Livre"
        elif "amazon" in my_link:
            label = "🛒 Comprar na Amazon"
        elif "shopee" in my_link:
            label = "🛒 Comprar na Shopee"
        elif "magazinevoce" in my_link or "magazineluiza" in my_link:
            label = "🛒 Comprar no Magalu"
        else:
            label = "🛒 Comprar aqui"
            
        anchor = f'<a href="{_html.escape(my_link, quote=True)}">{label}</a>'
        
        # Formata o texto destacando instruções e códigos de CUPOM para o usuário copiar facilmente
        formatted_lines = []
        for line in orig_text.split("\n"):
            l_strip = line.strip()
            # Se a linha menciona cupom, coloca destaque visual e código monoespaçado (clique para copiar)
            if re.search(r'\bcupom\b', l_strip, re.IGNORECASE):
                m_code = re.search(r'cupom[:\s]+([A-Za-z0-9_\-]+)', l_strip, re.IGNORECASE)
                palavra = m_code.group(1) if m_code else ""
                ignorar = ["de", "do", "da", "no", "na", "para", "por", "em", "um", "uma", "com", "sem", "casa"]
                if palavra and len(palavra) >= 4 and palavra.lower() not in ignorar:
                    codigo = palavra.upper()
                    formatted_lines.append(f"🎟️ <b>Cupom:</b> <code>{codigo}</code> <i>(toque para copiar)</i>")
                else:
                    limpa = l_strip.lstrip("-*• ").strip()
                    formatted_lines.append(f"🎟️ <b>{_html.escape(limpa)}</b>")
            else:
                formatted_lines.append(_html.escape(line, quote=False))
                
        escaped_text = "\n".join(formatted_lines)
        final_text = escaped_text.replace(_html.escape(orig_link, quote=False), anchor)
        if anchor not in final_text:
            final_text += "\n\n" + anchor
        
        from bot.core import Offer, send_telegram
        dummy_offer = Offer(
            title=prod_title[:80],
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
                    (uid, prod_title[:150], "espelho", 0.0, my_link)
                )
                db.commit()
            sent += 1
            log.info("Oferta espelhada com sucesso do canal @%s: %s", channel_username, my_link)
            
    return sent
