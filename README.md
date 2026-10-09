# Bot de Ofertas (Telegram)

Coleta promoções de feeds RSS, filtra, duplica-checa (SQLite), monta o link de afiliado e publica no Telegram.

## Configuração
1. `pip install -r requirements.txt`
2. Crie um bot com o @BotFather, adicione-o como **administrador** do seu canal/grupo.
3. Copie `.env.example` para `.env` e preencha `TELEGRAM_BOT_TOKEN`.
4. Edite `config.json`: feeds, canais (`@canal` ou ID), filtros e regras de afiliado por loja.
5. Teste sem enviar: `python main.py --once`
6. Publicar de verdade: `python main.py --live` (roda em loop; `--once` para um ciclo).

## Afiliados
- `tag_param`/`tag_value`: acrescenta sua tag à URL (ex.: Amazon Associates).
- `template`: deeplink de redes como Awin (`{url}` ou `{url_encoded}`).
- Só lojas listadas em `affiliate` são publicadas.

## Avisos
- **Confirme os termos de cada programa**: o do Mercado Livre, segundo informado, proíbe divulgar no WhatsApp/Telegram. Não está configurado aqui.
- Verifique também os termos do site dos feeds que usar.
- Informe que há comissão (campo `disclaimer`).
- WhatsApp: use apenas a API oficial (WhatsApp Business Platform); fica para uma próxima etapa.
