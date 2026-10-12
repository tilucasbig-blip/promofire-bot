"""Executa o bot. Uso: python main.py [--once] [--live]
Por padrão roda em DRY-RUN (não envia nada); use --live para publicar de verdade."""
import argparse
import http.server
import logging
import os
import threading
import time

from bot.core import (already_posted, affiliate_url, fetch_feed, fetch_gatry_amazon, format_message, init_db,
                      load_config, mark_posted, passes_filters, send_telegram)
from bot.shopee_batch import load_shopee_batch_offers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")


def start_health_server():
    """Abre uma porta web e faz auto-ping a cada 5 minutos para o Render nunca hibernar."""
    port = int(os.environ.get("PORT", 8080))
    class HealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"PromoFire Bot is LIVE and Healthy!")
        def log_message(self, *args):
            pass
    try:
        server = http.server.HTTPServer(("0.0.0.0", port), HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log.info("Servidor de Healthcheck iniciado na porta %d", port)
    except Exception as e:
        log.warning("Nao foi possivel iniciar servidor web: %s", e)

    # Thread de Auto-Ping para manter o Render acordado 24/7
    def keep_alive():
        import requests
        render_url = os.environ.get("RENDER_EXTERNAL_URL", f"http://127.0.0.1:{port}")
        while True:
            time.sleep(240)  # A cada 4 minutos dá um ping
            try:
                requests.get(render_url, timeout=10)
            except Exception:
                pass

    threading.Thread(target=keep_alive, daemon=True).start()


def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)


def run_cycle(cfg, db, token, dry_run):
    sent = 0
    max_posts = cfg["schedule"]["max_posts_per_cycle"]
    
    all_offers = []

    # 1. Coleta produtos reais da Amazon em tempo real
    try:
        amazon_offers = fetch_gatry_amazon()
        log.info("Coletadas %d ofertas reais da Amazon", len(amazon_offers))
        all_offers.extend(amazon_offers)
    except Exception as e:
        log.error("Falha ao coletar ofertas da Amazon: %s", e)

    # 2. Coleta feeds de produtos reais (Mercado Livre, Shopee, etc.)
    for feed in cfg.get("feeds", []):
        try:
            offers = fetch_feed(feed)
            all_offers.extend(offers)
        except Exception as e:
            log.error("Falha no feed %s: %s", feed, e)
            continue

    for o in all_offers:
        if sent >= max_posts:
            return sent
        # Exige estritamente produto individual: com foto e com preço!
        if not o.image or not o.price:
            continue
        if already_posted(db, o) or not passes_filters(o, cfg):
            continue
        link = affiliate_url(o, cfg)
        if not link:
            continue
        text = format_message(o, link, cfg)
        ok = True
        for chat in cfg["telegram_chats"]:
            ok = send_telegram(token, chat, o, text, dry_run) and ok
            time.sleep(cfg["schedule"].get("delay_between_posts_sec", 3))
        if ok:
            if not dry_run:
                mark_posted(db, o, link)
            sent += 1
    return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="roda um ciclo e sai")
    ap.add_argument("--live", action="store_true", help="publica de verdade")
    args = ap.parse_args()
    load_env()
    cfg = load_config("config.json")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if args.live and not token:
        raise SystemExit("Defina TELEGRAM_BOT_TOKEN no arquivo .env")
    
    # Inicia o servidor de saúde para o Render
    start_health_server()
    
    db = init_db(cfg.get("database", "ofertas.db"))
    while True:
        n = run_cycle(cfg, db, token, dry_run=not args.live)
        if n > 0:
            log.info("Ciclo concluído: %d nova(s) oferta(s) postada(s)!", n)
        if args.once:
            break
        # Intervalo rápido de 45 segundos para postar assim que a promoção sair
        poll_interval = cfg.get("schedule", {}).get("poll_interval_seconds", 45)
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
