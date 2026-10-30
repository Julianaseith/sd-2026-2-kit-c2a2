"""
Servico 3 - GERACAO: monta o prompt com o contexto e chama o LLM.

TAREFA 3: proteger a chamada ao LLM com o circuit breaker,
tempo-limite e retentativas.

Rodar: uvicorn servicos.geracao.app:app --port 8003
"""
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as TempoEsgotado
from typing import Literal

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from servicos.comum.circuit_breaker import CircuitBreaker, CircuitoAberto
from servicos.comum.llm import gerar

app = FastAPI(title="Geracao - C2.A2")
log = logging.getLogger("uvicorn.error")

URL_RECUPERACAO = os.getenv("URL_RECUPERACAO", "http://localhost:8002")
TIMEOUT_LLM = float(os.getenv("TIMEOUT_LLM", "5"))
MAX_TENTATIVAS = int(os.getenv("MAX_TENTATIVAS", "3"))
INTERVALO_TENTATIVA = float(os.getenv("INTERVALO_TENTATIVA", "1"))

disjuntor = CircuitBreaker(
    limite_falhas=int(os.getenv("LIMITE_FALHAS", "3")),
    espera_segundos=float(os.getenv("ESPERA_DISJUNTOR", "20")),
)
disjuntor_recuperacao = CircuitBreaker(limite_falhas=3, espera_segundos=20)

simulacao = {"modo": "nenhuma", "falhas_restantes": 0}
trava_simulacao = threading.Lock()


class Pergunta(BaseModel):
    pergunta: str


class Simulacao(BaseModel):
    modo: Literal["nenhuma", "transitoria", "timeout", "indisponivel"] = "nenhuma"
    falhas: int = Field(default=2, ge=0)


def buscar_trechos(pergunta):
    r = requests.post(f"{URL_RECUPERACAO}/buscar",
                      json={"pergunta": pergunta, "top_k": 3}, timeout=10)
    r.raise_for_status()
    return r.json()["resultados"]


def chamar_llm(pergunta, contexto):
    modo = simulacao["modo"]
    if modo == "timeout":
        time.sleep(TIMEOUT_LLM + 2)
    elif modo == "indisponivel":
        raise ConnectionError("LLM indisponivel (simulado)")
    elif modo == "transitoria":
        with trava_simulacao:
            if simulacao["falhas_restantes"] > 0:
                simulacao["falhas_restantes"] -= 1
                raise ConnectionError("falha transitoria (simulada)")
    return gerar(pergunta, contexto)


def com_timeout(funcao, *args):
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(funcao, *args).result(timeout=TIMEOUT_LLM)
    finally:
        executor.shutdown(wait=False)


def falha_transitoria(erro):
    if isinstance(erro, (TempoEsgotado, ConnectionError,
                         requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(erro, requests.HTTPError) and erro.response is not None:
        return erro.response.status_code == 429 or erro.response.status_code >= 500
    return False


def gerar_com_retentativas(pergunta, contexto, info):
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        info["tentativas"] = tentativa
        try:
            return com_timeout(chamar_llm, pergunta, contexto)
        except Exception as erro:
            if not falha_transitoria(erro) or tentativa == MAX_TENTATIVAS:
                raise
            espera = INTERVALO_TENTATIVA * 2 ** (tentativa - 1)
            log.warning("LLM: tentativa %d/%d falhou (%s), nova tentativa em %.1fs",
                        tentativa, MAX_TENTATIVAS, type(erro).__name__, espera)
            time.sleep(espera)


def erro_llm(status, mensagem, contexto, info):
    log.error("LLM: %s | tentativas=%d | disjuntor=%s (%d falhas)",
              mensagem, info["tentativas"], disjuntor.estado, disjuntor.falhas)
    return HTTPException(status_code=status, detail={
        "erro": mensagem,
        "disjuntor": disjuntor.estado,
        "tentativas": info["tentativas"],
        "contexto": contexto,
    })


@app.post("/perguntar")
def perguntar(p: Pergunta):
    try:
        resultados = disjuntor_recuperacao.chamar(buscar_trechos, p.pergunta)
    except CircuitoAberto:
        raise HTTPException(status_code=503, detail="Recuperacao bloqueada pelo disjuntor: tente novamente em instantes")
    except requests.RequestException:
        raise HTTPException(status_code=503, detail="Servico de recuperacao indisponivel")

    contexto = [t["texto"] for t in resultados]
    fontes = list(dict.fromkeys(t["origem"] for t in resultados))
    if not contexto:
        return {"pergunta": p.pergunta, "resposta": "Nenhum documento indexado para responder a pergunta.",
                "contexto": [], "fontes": []}

    info = {"tentativas": 0}
    try:
        resposta = disjuntor.chamar(gerar_com_retentativas, p.pergunta, contexto, info)
    except CircuitoAberto:
        raise erro_llm(503, "LLM temporariamente indisponivel: circuito aberto", contexto, info)
    except TempoEsgotado:
        raise erro_llm(504, f"LLM nao respondeu em {TIMEOUT_LLM:.0f}s", contexto, info)
    except Exception as erro:
        raise erro_llm(503, f"Falha ao chamar o LLM: {erro}", contexto, info)

    return {"pergunta": p.pergunta, "resposta": resposta, "contexto": contexto,
            "fontes": fontes, "tentativas": info["tentativas"]}


@app.post("/simulacao", tags=["testes de falha"])
def configurar_simulacao(s: Simulacao):
    with trava_simulacao:
        simulacao["modo"] = s.modo
        simulacao["falhas_restantes"] = s.falhas
    return simulacao


@app.get("/saude")
def saude():
    return {"status": "ok", "disjuntor": disjuntor.estado, "falhas": disjuntor.falhas,
            "disjuntor_recuperacao": disjuntor_recuperacao.estado,
            "simulacao": simulacao["modo"]}
