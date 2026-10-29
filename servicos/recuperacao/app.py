
"""
Servico 2 - RECUPERACAO: dada uma pergunta,
acha os trechos mais relevantes.

TAREFA 2: implementar a busca por similaridade.

Rodar: uvicorn servicos.recuperacao.app:app --port 8002
"""

import os

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from servicos.comum.embeddings import Vetorizador, similaridade

app = FastAPI(title="Recuperacao - C2.A2")

URL_INGESTAO = os.getenv(
    "URL_INGESTAO",
    "http://localhost:8001"
)


class Consulta(BaseModel):
    pergunta: str
    top_k: int = Field(default=3, ge=1)


@app.post("/buscar")
def buscar(consulta: Consulta):
    """Devolve os top_k trechos mais parecidos com a pergunta."""

    # Busca os documentos indexados no servico de ingestao.
    try:
        r = requests.get(
            f"{URL_INGESTAO}/indice",
            timeout=10
        )
        r.raise_for_status()

    except requests.RequestException:
        raise HTTPException(
            status_code=503,
            detail="Servico de ingestao indisponivel"
        )

    itens = r.json()["itens"]

    # Caso ainda nao existam documentos.
    if not itens:
        return {"resultados": []}

    # Ajusta o vocabulario com os textos indexados.
    textos = [item["texto"] for item in itens]

    vetorizador = Vetorizador()
    vetorizador.ajustar(textos)

    # Transforma a pergunta em vetor.
    vetor_pergunta = vetorizador.vetorizar(
        consulta.pergunta
    )

    resultados = []

    # Compara a pergunta com cada documento.
    for item in itens:
        pontuacao = similaridade(
            vetor_pergunta,
            item["vetor"]
        )

        resultados.append({
            "texto": item["texto"],
            "origem": item["origem"],
            "similaridade": pontuacao
        })

    # Ordena do mais relevante para o menos relevante.
    resultados.sort(
        key=lambda item: item["similaridade"],
        reverse=True
    )

    # Retorna somente a quantidade solicitada.
    melhores = resultados[:consulta.top_k]

    return {"resultados": melhores}


@app.get("/saude")
def saude():
    return {"status": "ok"}
