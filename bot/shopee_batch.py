"""Módulo para carregar links em massa da Shopee a partir de arquivos CSV."""
import csv
import glob
import os
from typing import List
from bot.core import Offer

# Tradução amigável das categorias da Shopee para português com emojis chamativos
TRANSLATIONS = {
    "Health": ("Saúde & Cuidados", "Cuide da sua saúde com as melhores ofertas e descontos!"),
    "Fashion Accessories": ("Acessórios de Moda", "Acessórios, relógios, óculos e bijuterias com até 70% OFF!"),
    "Home Appliances": ("Eletrodomésticos & Casa", "Air fryers, cafeteiras, aspiradores e utilidades com preços imperdíveis!"),
    "Men Clothes": ("Moda Masculina", "Camisetas, bermudas, calças e jaquetas no precinho!"),
    "Men Shoes": ("Calçados Masculinos", "Tênis, sapatos e chinelos com frete grátis e desconto!"),
    "Mobile & Gadgets": ("Celulares & Acessórios", "Smartphones, capinhas, carregadores e fones bluetooth em super oferta!"),
    "Travel & Luggage": ("Malas & Viagem", "Malas, mochilas e organizadores de viagem em promoção!"),
    "Women Bags": ("Bolsas Femininas", "Bolsas, carteiras e mochilas femininas em alta!"),
    "Women Clothes": ("Moda Feminina", "Looks, vestidos, blusas e tendências com desconto!"),
    "Women Shoes": ("Calçados Femininos", "Sandálias, tênis, saltos e rasteirinhas baratas!"),
    "Computers & Accessories": ("Informática & PC Gamer", "Teclados, mouses, monitores e periféricos em oferta!"),
    "Video Games": ("Games & Consoles", "Jogos, controles, acessórios para PS, Xbox e PC Gamer!"),
    "Beauty": ("Beleza & Perfumaria", "Skincare, maquiagens e perfumes importados e nacionais!"),
    "Sports & Outdoors": ("Esportes & Fitness", "Roupas fitness, suplementos e artigos esportivos com desconto!"),
    "Home & Living": ("Casa, Mesa & Banho", "Decoração, cama, mesa, banho e organização para seu lar!"),
    "Audio": ("Fones & Caixas de Som", "Fones TWS sem fio e caixas de som bluetooth potentes!"),
    "Motors": ("Automotivo & Moto", "Acessórios e peças para carro e moto com preço baixo!"),
    "Watches": ("Relógios & Smartwatches", "Relógios elegantes e smartwatches com funções completas!"),
    "Food & Beverages": ("Alimentos & Bebidas", "Snacks, cafés, chocolates e guloseimas no precinho!"),
    "Stationery": ("Papelaria & Escritório", "Itens de papelaria fofa, cadernos e escritório!"),
    "Toys": ("Brinquedos & Hobbies", "Brinquedos, jogos de tabuleiro e colecionáveis!"),
    "Mom & Baby": ("Bebês & Maternidade", "Fraldas, roupinhas e cuidados com o bebê com desconto!"),
    "Pet Care": ("Pet Shop", "Rações, petiscos e brinquedos para cães e gatos!"),
}


def load_shopee_batch_offers(folder_path: str = r"C:\Users\Lucas Nunes\Downloads\link massa") -> List[Offer]:
    """Lê todos os CSVs da pasta e monta a lista de ofertas."""
    offers = []
    if not os.path.exists(folder_path):
        return offers

    csv_files = glob.glob(os.path.join(folder_path, "*.csv"))
    for file in csv_files:
        try:
            with open(file, encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    raw_name = row.get("Offer Name", "").replace("- - ", "").strip()
                    link = row.get("Offer Link", "").strip()
                    if not raw_name or not link:
                        continue

                    friendly_title, description = TRANSLATIONS.get(
                        raw_name, (raw_name, "Confira os melhores descontos e cupons disponíveis hoje na Shopee!")
                    )

                    title = f"Shopee: Especial {friendly_title} 🔥"
                    offers.append(
                        Offer(
                            title=title,
                            url=link,
                            store="shopee.com.br",
                            price=None,
                            old_price=None,
                            image=None,  # Pode ser preenchido ou usar imagem ilustrativa
                        )
                    )
        except Exception as e:
            pass

    return offers
