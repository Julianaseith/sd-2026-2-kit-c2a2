
"""
Servico 1 - INGESTAO: recebe documentos, gera embeddings e armazena.

TAREFA 1: persistir o indice em disco para nao perder ao reiniciar.

Rodar: uvicorn servicos.ingestao.app:app --port 8001
"""
import redis    
import os
import json
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from servicos.comum.embeddings import Vetorizador

app = FastAPI(title="Ingestao - C2.A2")

vetorizador = Vetorizador()
INDICE = []

# Arquivo onde os documentos serao armazenados.
ARQUIVO_INDICE = Path(
    os.getenv(
        "ARQUIVO_INDICE",
        str(Path(__file__).resolve().parent / "indice.json")
    )
)
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

cliente_redis = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True
)

class Documento(BaseModel):
    texto: str
    origem: str = "desconhecida"


def salvar_indice():
    """Salva os documentos indexados em disco."""
    documentos = [
        {"texto": item["texto"], "origem": item["origem"]}
        for item in INDICE
    ]
    ARQUIVO_INDICE.parent.mkdir(parents=True, exist_ok=True)
    with open(ARQUIVO_INDICE, "w", encoding="utf-8") as arquivo:
        json.dump(documentos, arquivo, ensure_ascii=False, indent=4)


def carregar_indice():
    """Carrega os documentos salvos e reconstrói os vetores."""
    if not ARQUIVO_INDICE.exists():
        return

    with open(ARQUIVO_INDICE, "r", encoding="utf-8") as arquivo:
        documentos = json.load(arquivo)

    if not documentos:
        return

    textos = [item["texto"] for item in documentos]

    vetorizador.ajustar(textos)

    INDICE.clear()

    for item in documentos:
        INDICE.append({
            "texto": item["texto"],
            "origem": item["origem"],
            "vetor": vetorizador.vetorizar(item["texto"])
        })


@app.post("/indexar")
def indexar(docs: list[Documento]):
    # Mantem os documentos anteriores e adiciona os novos.
    documentos = [
        {"texto": item["texto"], "origem": item["origem"]}
        for item in INDICE
    ]

    for d in docs:
        documentos.append({
            "texto": d.texto,
            "origem": d.origem
        })

    if documentos:
        textos = [item["texto"] for item in documentos]
        vetorizador.ajustar(textos)

        INDICE.clear()

        for item in documentos:
            INDICE.append({
                "texto": item["texto"],
                "origem": item["origem"],
                "vetor": vetorizador.vetorizar(item["texto"])
            })

    salvar_indice()

    return {"indexados": len(INDICE)}



@app.post("/indexar-async", status_code=202)
def indexar_async(docs: list[Documento]):
    """Envia documentos para processamento pela fila Redis."""

    documentos = [
        {"texto": d.texto, "origem": d.origem}
        for d in docs
    ]

    try:
        cliente_redis.rpush(
            "fila_ingestao",
            json.dumps(documentos, ensure_ascii=False)
        )

    except redis.RedisError:
        raise HTTPException(
            status_code=503,
            detail="Fila Redis indisponivel"
        )

    return {
        "status": "enfileirado",
        "documentos_recebidos": len(documentos)
    }



@app.get("/indice")
def indice():
    """Consumido pelo servico de recuperacao."""
    return {
        "total": len(INDICE),
        "itens": [
            {
                "texto": i["texto"],
                "origem": i["origem"],
                "vetor": i["vetor"]
            }
            for i in INDICE
        ]
    }


@app.get("/saude")
def saude():
    return {"status": "ok", "documentos": len(INDICE)}


# Recupera os documentos salvos quando o servico inicia.
carregar_indice()
