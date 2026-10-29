
"""
Worker de mensageria - C2.A2.

Consome documentos da fila Redis e envia
para o microsservico de ingestao.
"""

import json
import os
import time

import redis
import requests

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

URL_INGESTAO = os.getenv(
    "URL_INGESTAO",
    "http://localhost:8001"
)

NOME_FILA = "fila_ingestao"

cliente_redis = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True
)


def processar_documentos(documentos):
    """Envia documentos para o servico de ingestao."""

    resposta = requests.post(
        f"{URL_INGESTAO}/indexar",
        json=documentos,
        timeout=15
    )

    resposta.raise_for_status()

    print("Documentos indexados:", resposta.json(), flush=True)


def iniciar_worker():
    print("Worker de ingestao iniciado.", flush=True)

    while True:
        try:
            print("Aguardando documentos...", flush=True)

            resultado = cliente_redis.blpop(
                NOME_FILA,
                timeout=5
            )

            if resultado is None:
                continue

            _, mensagem = resultado
            documentos = json.loads(mensagem)

            try:
                processar_documentos(documentos)

            except requests.RequestException as erro:
                print("Erro na ingestao:", erro, flush=True)

                # Recoloca a mensagem na fila.
                cliente_redis.rpush(NOME_FILA, mensagem)
                time.sleep(3)

        except redis.RedisError as erro:
            print("Erro de conexao com Redis:", erro, flush=True)
            time.sleep(3)

        except (ValueError, TypeError) as erro:
            print("Mensagem invalida:", erro, flush=True)


if __name__ == "__main__":
    iniciar_worker()
